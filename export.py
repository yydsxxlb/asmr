"""Export graph.sqlite -> sharded static JSON for the site (GitHub-Pages ready).

Writes into web/data/:
  index.json          { file_origin, generated, mounts:[{name,path,id}], counts }
  tree/root.json      children of "/"  (the mount dirs, each with its shard id)
  tree/<id>.json      children of one directory; dir children carry their own id
  names.json          flat search index of PLAYABLE media: [path, ...]
  subs.json           [ {media, sub, text} ]   (subtitle search index)

Per-directory sharding keeps first paint tiny: the site fetches index.json +
names.json up front, then pulls one small tree/<id>.json per folder as it is
opened. A dir child entry carries `id` = the shard holding its children, so the
tree is self-describing (no global path->id map needed). Dirs with no children
get id=null (rendered as empty, no fetch). Dir entries also carry `n`/`sz` =
recursive descendant file count / total bytes.

Audio streams straight from file_origin (no proxy) via <meta referrer=no-referrer>
so the origin Referer hotlink-check passes.

Run after a crawl:  python crawler/crawl.py && python export.py
"""
import json
import os
import sqlite3
import time

from common import DB_PATH, FILE_ORIGIN

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "data")
TREE = os.path.join(OUT, "tree")

# kinds/exts that the player can open in-app (searchable index)
VIDEO_EXT = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}
PLAYABLE_KINDS = {"audio", "hls"}


def _ext(name):
    i = name.rfind(".")
    return name[i:].lower() if i >= 0 else ""


def main():
    os.makedirs(TREE, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    if "cues" not in {r[1] for r in c.execute("PRAGMA table_info(subs)")}:
        c.execute("ALTER TABLE subs ADD COLUMN cues TEXT")

    children = {}                              # parent path -> [entry, ...]

    for r in c.execute("SELECT path,name,parent FROM nodes WHERE is_dir=1"):
        children.setdefault(r["parent"] or "/", []).append(
            {"path": r["path"], "name": r["name"], "is_dir": 1})

    names = []                                 # playable media paths (search idx)
    for r in c.execute(
            "SELECT path,name,parent,size,kind FROM nodes WHERE is_dir=0"):
        children.setdefault(r["parent"] or "/", []).append(
            {"path": r["path"], "name": r["name"], "is_dir": 0,
             "kind": r["kind"], "size": r["size"]})
        if r["kind"] in PLAYABLE_KINDS or _ext(r["name"]) in VIDEO_EXT:
            names.append(r["path"])

    # synthesize mount-root dirs under "/"
    mounts = sorted({p.split("/")[1] for p in children
                     if p.count("/") == 1 and len(p) > 1})
    children["/"] = [{"path": "/" + m, "name": m, "is_dir": 1} for m in mounts]

    # assign an integer shard id to every dir that actually has children
    idmap = {}                                 # dir path -> shard id
    for i, dpath in enumerate(k for k in children if k != "/"):
        idmap[dpath] = i

    # recursive per-dir totals: every file's size/count rolls up to all of its
    # ancestor dirs (one pass over leaves, walking the parent chain to the mount).
    totals = {}                                # dir path -> [n_files, bytes]
    for rows in children.values():
        for x in rows:
            if x["is_dir"]:
                continue
            sz = x.get("size") or 0
            d = x["path"].rsplit("/", 1)[0]
            while d:
                t = totals.setdefault(d, [0, 0])
                t[0] += 1
                t[1] += sz
                if "/" not in d[1:]:           # reached mount root (/asmr)
                    break
                d = d.rsplit("/", 1)[0]

    def prep(rows):
        """Sort dirs-first; stamp dir entries with shard id + recursive totals."""
        rows.sort(key=lambda x: (x["is_dir"] == 0, x["name"]))
        for x in rows:
            if x["is_dir"]:
                x["id"] = idmap.get(x["path"])   # None => empty dir
                n, b = totals.get(x["path"], (0, 0))
                x["n"] = n                       # descendant file count
                x["sz"] = b                      # total bytes
        return rows

    # write per-dir shards
    for dpath, sid in idmap.items():
        with open(os.path.join(TREE, f"{sid}.json"), "w", encoding="utf-8") as f:
            json.dump(prep(children[dpath]), f,
                      ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(TREE, "root.json"), "w", encoding="utf-8") as f:
        json.dump(prep(children["/"]), f,
                  ensure_ascii=False, separators=(",", ":"))

    n_files = sum(1 for rows in children.values() for x in rows
                  if not x["is_dir"])
    index = {
        "file_origin": FILE_ORIGIN,
        "generated": time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime()),
        "mounts": [{"name": m, "path": "/" + m, "id": idmap.get("/" + m)}
                   for m in mounts],
        "counts": {"files": n_files, "playable": len(names),
                   "dirs": len(idmap)},
    }

    subs, lyrics = [], {}
    for r in c.execute("SELECT media_path,sub_path,text,cues FROM subs "
                       "WHERE text<>''"):
        subs.append({"media": r["media_path"], "sub": r["sub_path"],
                     "text": r["text"]})
        if r["cues"] and r["media_path"]:          # media -> timed cues
            try:
                lyrics[r["media_path"]] = json.loads(r["cues"])
            except (ValueError, TypeError):
                pass

    with open(os.path.join(OUT, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(OUT, "names.json"), "w", encoding="utf-8") as f:
        json.dump(names, f, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(OUT, "subs.json"), "w", encoding="utf-8") as f:
        json.dump(subs, f, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(OUT, "lyrics.json"), "w", encoding="utf-8") as f:
        json.dump(lyrics, f, ensure_ascii=False, separators=(",", ":"))

    # drop the old monolith so stale data can't be served
    old = os.path.join(OUT, "catalog.json")
    if os.path.exists(old):
        os.remove(old)

    print(f"index.json: {n_files} files, {len(idmap)} dir shards, "
          f"{len(mounts)} mounts")
    print(f"names.json: {len(names)} playable")
    print(f"subs.json: {len(subs)} subtitle docs")
    print(f"lyrics.json: {len(lyrics)} media with timed cues")
    c.close()


if __name__ == "__main__":
    main()
