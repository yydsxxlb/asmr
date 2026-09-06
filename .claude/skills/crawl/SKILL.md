---
name: crawl
description: Refresh the ASMR catalog — crawl the AList backend into graph.sqlite (incremental, prunes deletions) then rebuild the static site data. Use for "crawl", "refresh catalog", "update the catalog", "re-scan the backend".
---

# Refresh the catalog

Two steps, always in this order. Run from the repo root.

## 1. Crawl the backend into `graph.sqlite`

```bash
python crawler/crawl.py            # incremental: skips dirs whose modified is unchanged
```

Flags:
- `--force` — re-list every dir (ignore `crawl_state`). Use for a full prune sweep
  of deletions that predate incremental tracking.
- `--mount asmr` — one mount only.
- `--max-dirs 40` — bounded demo crawl.
- `--workers 8` — parallel requests (default 8).

The crawler is resumable and **prunes** entries deleted upstream (removes the node
+ its subtree from `nodes`/`files_fts`/`subs`/`subs_fts`/`crawl_state`). The final
line reports `listed= failed= pruned= files= audio= subs_indexed=`.

Note: mounts `asmr3`/`asmr5` are empty — backend 500 (`SharePoint Online` tenant
has no SPO license). Nothing to crawl there.

## 2. Rebuild the site data

```bash
python export.py
```

Regenerates the sharded catalog under `web/data/` (`index.json`, `tree/<id>.json`,
`names.json`, `subs.json`) with per-folder `n`/`sz` totals.

## Verify

- `export.py` prints `index.json: <files> files, <dirs> dir shards, <mounts> mounts`.
- To publish the refreshed data, run the **deploy** skill (commit + push → Pages).
