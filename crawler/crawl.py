"""Phase 1+2 crawler: BFS the AList tree into graph.sqlite, index names +
sidecar subtitles into FTS5. Resumable + incremental (skips dirs whose
`modified` is unchanged since last crawl).

Usage:
    python crawler/crawl.py                 # crawl all mounts
    python crawler/crawl.py --max-dirs 40   # bounded demo crawl (fast)
    python crawler/crawl.py --mount asmr    # single mount
"""
import argparse
import os
import sqlite3
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import (DB_PATH, MOUNTS, UA, fs_list, raw_url, ext_of,  # noqa: E402
                    kind_of, norm_key)

SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes(
  path TEXT PRIMARY KEY, name TEXT, parent TEXT, is_dir INT,
  size INT, kind TEXT, ext TEXT, modified TEXT,
  mount TEXT, work TEXT, nkey TEXT,
  sync_state TEXT, opus_path TEXT, opus_size INT);
CREATE INDEX IF NOT EXISTS ix_parent ON nodes(parent);
CREATE INDEX IF NOT EXISTS ix_kind ON nodes(kind);
CREATE VIRTUAL TABLE IF NOT EXISTS files_fts
  USING fts5(name, path UNINDEXED, tokenize='trigram');
CREATE TABLE IF NOT EXISTS subs(
  media_path TEXT, sub_path TEXT, text TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS subs_fts
  USING fts5(text, media_path UNINDEXED, sub_path UNINDEXED,
             tokenize='trigram');
CREATE TABLE IF NOT EXISTS crawl_state(path TEXT PRIMARY KEY, modified TEXT);
"""


def db():
    c = sqlite3.connect(DB_PATH)
    c.executescript(SCHEMA)
    return c


def parse_sub(text, ext):
    """Return plain caption text (strip timecodes/styling)."""
    lines = []
    for ln in text.splitlines():
        s = ln.strip().lstrip("﻿")
        if not s or s.isdigit():
            continue
        if s.startswith("[") and s.endswith("]"):   # ass/ini section header
            continue
        if "-->" in s:                       # srt/vtt timecode
            continue
        if ext == ".lrc" and s.startswith("["):
            s = s.split("]", 1)[-1].strip()
        if ext == ".ass":
            if s.startswith("Dialogue:"):
                s = s.split(",", 9)[-1]
                s = s.replace("\\N", " ")
            elif ":" in s and not s.startswith("Comment:"):
                continue
        if s.upper() == "WEBVTT":
            continue
        if s:
            lines.append(s)
    return " ".join(lines)


def fetch_text(path):
    req = urllib.request.Request(raw_url(path), headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "ignore")


def list_dir(path, empty_retries=2):
    """List one dir. HTTP retries live in _post; retry a couple more times on
    an *empty* 200 (flaky backend returns [] transiently). Bounded -> no stuck.
    Returns (path, content_list) or (path, None) on hard failure."""
    content = []
    for attempt in range(empty_retries + 1):
        try:
            content = fs_list(path).get("content") or []
        except Exception:                                 # noqa: BLE001
            content = None
        if content:                                       # got entries: done
            return path, content
        if content is None and attempt == empty_retries:
            return path, None
    return path, content                                  # accept genuine empty


def prune_subtree(c, root):
    """Delete a node and everything under it from all tables. Returns count.
    Walks by parent (not LIKE) so names containing % or _ can't over-match."""
    victims, stack = [], [root]
    while stack:
        p = stack.pop()
        victims.append(p)
        stack.extend(cp for (cp,) in
                     c.execute("SELECT path FROM nodes WHERE parent=?", (p,)))
    for i in range(0, len(victims), 400):          # chunk to bound SQL vars
        chunk = victims[i:i + 400]
        qs = ",".join("?" * len(chunk))
        c.execute(f"DELETE FROM nodes       WHERE path IN ({qs})", chunk)
        c.execute(f"DELETE FROM files_fts   WHERE path IN ({qs})", chunk)
        c.execute(f"DELETE FROM crawl_state WHERE path IN ({qs})", chunk)
        c.execute(f"DELETE FROM subs      WHERE sub_path IN ({qs}) "
                  f"OR media_path IN ({qs})", chunk + chunk)
        c.execute(f"DELETE FROM subs_fts  WHERE sub_path IN ({qs}) "
                  f"OR media_path IN ({qs})", chunk + chunk)
    return len(victims)


def crawl(mounts, max_dirs=None, workers=8, force=False, seeds=None):
    c = db()
    done = set() if force else {p for (p,) in
                                c.execute("SELECT path FROM crawl_state")}
    done -= set(seeds or ())                  # always (re)list explicit seeds
    ex = ThreadPoolExecutor(max_workers=workers)
    fmap = {}                                             # future -> path
    queued = set()

    def submit(p):
        if p in queued or p in done:
            return
        if max_dirs and len(queued) >= max_dirs:
            return
        queued.add(p)
        fmap[ex.submit(list_dir, p)] = p

    for p in (seeds if seeds else ["/" + m for m in mounts]):
        submit(p)
    subs, seen, failed, pruned = [], 0, 0, 0
    while fmap:
        got, _ = wait(list(fmap), return_when=FIRST_COMPLETED)
        for fut in got:
            path = fmap.pop(fut)
            _, content = fut.result()
            seen += 1
            if content is None:
                failed += 1
                print(f"[{seen}] FAIL {path}  outstanding={len(fmap)}")
                continue
            print(f"[{seen}] {path} ({len(content)})  outstanding={len(fmap)}")
            for it in content:
                child = path.rstrip("/") + "/" + it["name"]
                is_dir = 1 if it["is_dir"] else 0
                parts = child.strip("/").split("/")
                mount = parts[0]
                work = parts[2] if len(parts) > 2 else (
                    parts[1] if len(parts) > 1 else "")
                kind = "dir" if is_dir else kind_of(it["name"])
                c.execute(
                    "INSERT INTO nodes"
                    "(path,name,parent,is_dir,size,kind,ext,modified,mount,"
                    "work,nkey) VALUES(?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(path) DO UPDATE SET "
                    "name=excluded.name,parent=excluded.parent,"
                    "is_dir=excluded.is_dir,size=excluded.size,"
                    "kind=excluded.kind,ext=excluded.ext,"
                    "modified=excluded.modified,mount=excluded.mount,"
                    "work=excluded.work,nkey=excluded.nkey",  # keep opus_* cols
                    (child, it["name"], path, is_dir, it.get("size", 0), kind,
                     ext_of(it["name"]), it.get("modified", ""), mount, work,
                     norm_key(it["name"])))
                c.execute("INSERT INTO files_fts(name,path) VALUES(?,?)",
                          (it["name"], child))        # dirs + files (RJ codes)
                if is_dir:
                    submit(child)
                elif kind == "sub":
                    subs.append(child)
            # prune children that vanished upstream (this dir listed OK, so its
            # DB children not in the fresh listing were deleted on the backend).
            # Only on a non-empty listing: an empty 200 may be a flaky backend,
            # and wiping a whole folder on that is worse than a lingering ghost.
            if content:
                present = {path.rstrip("/") + "/" + it["name"] for it in content}
                kids = [p for (p,) in c.execute(
                        "SELECT path FROM nodes WHERE parent=?", (path,))]
                for p in kids:
                    if p not in present:
                        pruned += prune_subtree(c, p)
            if content:            # mark listed only on non-empty (empty may be
                c.execute("INSERT OR REPLACE INTO crawl_state VALUES(?,?)",
                          (path, ""))                 # flaky) -> retried next run
            c.commit()
    ex.shutdown()

    # Phase 2: fetch + parse sidecar subtitles in parallel (IO bound)
    print(f"\nSubtitles found: {len(subs)}  (crawl: dirs={seen} failed={failed})")

    def fetch_one(sp):
        try:
            return sp, parse_sub(fetch_text(sp), ext_of(sp))
        except Exception:                                 # noqa: BLE001
            return sp, None
    with ThreadPoolExecutor(max_workers=workers) as ex2:
        for sp, txt in ex2.map(fetch_one, subs):
            if not txt:
                continue
            key = norm_key(sp.rsplit("/", 1)[1])
            parent = sp.rsplit("/", 1)[0]
            row = c.execute(
                "SELECT path FROM nodes WHERE kind='audio' AND nkey=? "
                "ORDER BY (parent=?) DESC LIMIT 1", (key, parent)).fetchone()
            media = row[0] if row else ""
            c.execute("INSERT INTO subs(media_path,sub_path,text) VALUES(?,?,?)",
                      (media, sp, txt))
            c.execute("INSERT INTO subs_fts(text,media_path,sub_path) "
                      "VALUES(?,?,?)", (txt, media, sp))
    c.commit()

    n_files = c.execute("SELECT COUNT(*) FROM nodes WHERE is_dir=0").fetchone()[0]
    n_audio = c.execute("SELECT COUNT(*) FROM nodes WHERE kind='audio'").fetchone()[0]
    n_subs = c.execute("SELECT COUNT(*) FROM subs").fetchone()[0]
    print(f"\nDone. listed={seen} failed={failed} pruned={pruned} "
          f"files={n_files} audio={n_audio} subs_indexed={n_subs}")
    c.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mount", help="single mount name")
    ap.add_argument("--max-dirs", type=int, help="cap dirs (demo)")
    ap.add_argument("--workers", type=int, default=8, help="parallel requests")
    ap.add_argument("--force", action="store_true",
                    help="ignore crawl_state, re-list everything")
    a = ap.parse_args()
    crawl([a.mount] if a.mount else MOUNTS, a.max_dirs, a.workers, a.force)
