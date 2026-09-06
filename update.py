"""Refresh the static site data: incremental crawl, then export JSON.

    python update.py                # all mounts
    python update.py --mount asmr   # one mount
    python update.py --max-dirs 50  # bounded

Commit web/data/*.json to publish on GitHub Pages.
"""
import argparse

from crawler.crawl import crawl
from common import MOUNTS
import export

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mount")
    ap.add_argument("--max-dirs", type=int)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    crawl([a.mount] if a.mount else MOUNTS, a.max_dirs, a.workers, a.force)
    export.main()
