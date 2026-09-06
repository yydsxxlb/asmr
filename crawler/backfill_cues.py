"""Backfill timed cues for subtitle/lyric rows already in graph.sqlite.

The original crawl stored only stripped `subs.text` (timecodes discarded). This
re-fetches each known `sub_path` from the origin, parses timed cues, and fills
`subs.cues`. No BFS re-crawl — just the sub files. Idempotent; only touches rows
where `cues IS NULL`.

    python crawler/backfill_cues.py            # backfill missing cues
    python crawler/backfill_cues.py --all      # re-parse every sub row
"""
import argparse
import json
import os
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import DB_PATH, ext_of                       # noqa: E402
from crawler.crawl import fetch_text, parse_cues         # noqa: E402


def main(do_all=False, workers=8):
    c = sqlite3.connect(DB_PATH)
    cols = {r[1] for r in c.execute("PRAGMA table_info(subs)")}
    if "cues" not in cols:
        c.execute("ALTER TABLE subs ADD COLUMN cues TEXT")
    q = ("SELECT DISTINCT sub_path FROM subs WHERE sub_path<>''" +
         ("" if do_all else " AND cues IS NULL"))
    paths = [p for (p,) in c.execute(q)]
    print(f"backfilling {len(paths)} sub files…")

    def one(sp):
        try:
            return sp, parse_cues(fetch_text(sp), ext_of(sp))
        except Exception:                                 # noqa: BLE001
            return sp, None

    ok = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for sp, cues in ex.map(one, paths):
            if not cues:
                continue
            c.execute("UPDATE subs SET cues=? WHERE sub_path=?",
                      (json.dumps(cues, ensure_ascii=False), sp))
            ok += 1
    c.commit()
    print(f"done. cues filled for {ok}/{len(paths)} sub files")
    c.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="re-parse every sub row")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    main(a.all, a.workers)
