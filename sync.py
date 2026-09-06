"""Sync pipeline: download source audio -> encode to Opus 48kHz -> mark finished.

Pipeline (bounded, resumable, parallel):
  download worker(s)  --stream--> staging file --queue--> encode worker(s)
                                                          ffmpeg libopus 48k VBR
                                                          write web/media/<path>.opus
                                                          delete staging source
                                                          mark node sync_state='finished'

State on nodes: sync_state in ('unsynced','failed','finished'),
                opus_path (relative, under web/), opus_size.
Rerun skips 'finished'; retries 'failed'/'unsynced'.

  python sync.py                 # all pending audio
  python sync.py --mount asmr2   # one mount
  python sync.py --limit 100     # bounded test
  python sync.py --dl 16 --enc 24 --bitrate 48
"""
import argparse
import os
import queue
import sys
import shutil
import sqlite3
import subprocess
import threading
import time
import urllib.request

from common import DB_PATH, UA, fs_get, raw_url

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                          # noqa: BLE001
    pass

ROOT = os.path.dirname(os.path.abspath(__file__))
STAGING = os.path.join(ROOT, "downloads", "_staging")
MEDIA = os.path.join(ROOT, "web", "media")          # served by http.server --directory web
SENTINEL = object()


def migrate(c):
    cols = {r[1] for r in c.execute("PRAGMA table_info(nodes)")}
    for col, decl in (("sync_state", "TEXT DEFAULT 'unsynced'"),
                      ("opus_path", "TEXT"), ("opus_size", "INT")):
        if col not in cols:
            c.execute(f"ALTER TABLE nodes ADD COLUMN {col} {decl}")
    c.execute("CREATE INDEX IF NOT EXISTS ix_sync ON nodes(sync_state)")
    c.commit()


def reconcile(c):
    """Self-heal: mark nodes 'finished' whose .opus already exists on disk
    (e.g. a prior run encoded but lost the DB write to a lock)."""
    fixed = 0
    rows = c.execute("SELECT path FROM nodes WHERE kind='audio' "
                     "AND (sync_state IS NULL OR sync_state<>'finished')").fetchall()
    for (path,) in rows:
        rel = opus_rel(path)
        out = os.path.join(ROOT, "web", rel.replace("/", os.sep))
        try:
            sz = os.path.getsize(out)
        except OSError:
            continue
        if sz > 0:
            c.execute("UPDATE nodes SET sync_state='finished',opus_path=?,"
                      "opus_size=? WHERE path=?", (rel, sz, path))
            fixed += 1
    if fixed:
        c.commit()
    print(f"reconcile: marked {fixed} pre-existing opus as finished", flush=True)


def pending(c, mount, limit):
    q = ("SELECT path,size FROM nodes WHERE kind='audio' "
         "AND (sync_state IS NULL OR sync_state<>'finished')")
    args = []
    if mount:
        q += " AND mount=?"
        args.append(mount)
    q += " ORDER BY size ASC"          # small first = fast early wins
    if limit:
        q += f" LIMIT {int(limit)}"
    return [r[0] for r in c.execute(q, args)]


def opus_rel(path):
    """web-relative output path: media/<src path minus leading '/'>.opus"""
    rel = path.lstrip("/")
    base = rel.rsplit(".", 1)[0] if "." in os.path.basename(rel) else rel
    return "media/" + base + ".opus"


def stage_name(path):
    # flat unique staging name (avoid deep mkdirs for temp)
    return str(abs(hash(path))) + os.path.splitext(path)[1]


class Counter:
    def __init__(self, total):
        self.total, self.done, self.fail = total, 0, 0
        self.bytes = 0
        self.lock = threading.Lock()
        self.t0 = time.time()

    def tick(self, ok, nbytes=0):
        with self.lock:
            if ok:
                self.done += 1
                self.bytes += nbytes
            else:
                self.fail += 1
            n = self.done + self.fail
            if n % 25 == 0 or n == self.total:
                dt = time.time() - self.t0
                gb = self.bytes / 1e9
                rate = n / dt if dt else 0
                eta = (self.total - n) / rate / 60 if rate else 0
                print(f"[{n}/{self.total}] ok={self.done} fail={self.fail} "
                      f"{gb:.1f}GB out {rate:.1f} f/s ETA {eta:.0f}m", flush=True)


def db_writer(dbq):
    """Single serialized SQLite writer thread."""
    c = sqlite3.connect(DB_PATH, timeout=120)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=120000")
    n = 0
    while True:
        item = dbq.get()
        if item is SENTINEL:
            break
        sql, args = item
        for attempt in range(10):
            try:
                c.execute(sql, args)
                break
            except sqlite3.OperationalError as e:
                if "locked" in str(e) and attempt < 9:
                    time.sleep(1)
                    continue
                print(f"DB WRITE FAIL: {e} :: {args}", flush=True)
                break
        n += 1
        if n % 20 == 0:
            c.commit()
        dbq.task_done()
    c.commit()
    c.close()


def downloader(paths_q, enc_q, dbq, cnt):
    while True:
        path = paths_q.get()
        if path is SENTINEL:
            paths_q.task_done()
            break
        tmp = os.path.join(STAGING, stage_name(path))
        err = None
        for attempt in range(5):              # backoff on transient 403/429/5xx
            try:
                url = raw_url(path)            # bare works for all live mounts
                if attempt >= 1:              # on retry try signed (some mounts enforce)
                    try:
                        url = fs_get(path)["raw_url"]
                    except Exception:         # noqa: BLE001
                        pass
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=90) as r, \
                        open(tmp, "wb") as f:
                    shutil.copyfileobj(r, f, 1 << 20)
                enc_q.put((path, tmp))
                err = None
                break
            except Exception as e:            # noqa: BLE001
                err = e
                if os.path.exists(tmp):
                    os.remove(tmp)
                time.sleep(0.5 * (attempt + 1) ** 2)   # 0.5,2,4.5,8s
        if err is not None:
            dbq.put(("UPDATE nodes SET sync_state='failed' WHERE path=?", (path,)))
            cnt.tick(False)
            print(f"DL FAIL {path}: {err}", flush=True)
        paths_q.task_done()


def encoder(enc_q, dbq, cnt, bitrate):
    while True:
        item = enc_q.get()
        if item is SENTINEL:
            enc_q.task_done()
            break
        path, tmp = item
        rel = opus_rel(path)
        out = os.path.join(ROOT, "web", rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
               "-i", tmp, "-vn", "-map_metadata", "-1",
               "-c:a", "libopus", "-b:a", f"{bitrate}k", "-vbr", "on",
               "-application", "audio", "-ar", "48000", out]
        try:
            subprocess.run(cmd, check=True, capture_output=True)
            sz = os.path.getsize(out)
            dbq.put(("UPDATE nodes SET sync_state='finished',opus_path=?,"
                     "opus_size=? WHERE path=?", (rel, sz, path)))
            cnt.tick(True, sz)
        except subprocess.CalledProcessError as e:
            dbq.put(("UPDATE nodes SET sync_state='failed' WHERE path=?", (path,)))
            cnt.tick(False)
            err = e.stderr.decode("utf-8", "replace")[-200:] if e.stderr else ""
            print(f"ENC FAIL {path}: {err}", flush=True)
            if os.path.exists(out):
                os.remove(out)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
            enc_q.task_done()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mount")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--dl", type=int, default=16, help="download workers")
    ap.add_argument("--enc", type=int, default=24, help="encode workers")
    ap.add_argument("--bitrate", type=int, default=48)
    ap.add_argument("--qmax", type=int, default=64, help="staging queue cap")
    a = ap.parse_args()

    os.makedirs(STAGING, exist_ok=True)
    os.makedirs(MEDIA, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=120)
    c.execute("PRAGMA busy_timeout=120000")
    migrate(c)
    reconcile(c)
    todo = pending(c, a.mount, a.limit)
    c.close()
    if not todo:
        print("nothing pending.")
        return
    print(f"pending={len(todo)} dl={a.dl} enc={a.enc} bitrate={a.bitrate}k", flush=True)

    paths_q = queue.Queue()
    enc_q = queue.Queue(maxsize=a.qmax)     # bounds staging disk
    dbq = queue.Queue()
    cnt = Counter(len(todo))

    wt = threading.Thread(target=db_writer, args=(dbq,), daemon=True)
    wt.start()
    encs = [threading.Thread(target=encoder, args=(enc_q, dbq, cnt, a.bitrate),
                             daemon=True) for _ in range(a.enc)]
    dls = [threading.Thread(target=downloader, args=(paths_q, enc_q, dbq, cnt),
                            daemon=True) for _ in range(a.dl)]
    for t in encs + dls:
        t.start()
    for p in todo:
        paths_q.put(p)
    for _ in dls:
        paths_q.put(SENTINEL)
    for t in dls:
        t.join()
    # all real items now enqueued; one sentinel per encoder
    for _ in encs:
        enc_q.put(SENTINEL)
    for t in encs:
        t.join()
    dbq.put(SENTINEL)
    wt.join()
    print(f"DONE ok={cnt.done} fail={cnt.fail} out={cnt.bytes/1e9:.1f}GB", flush=True)


if __name__ == "__main__":
    main()
