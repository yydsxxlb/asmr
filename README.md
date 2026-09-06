# ASMR Mirror

Faster frontend over the AList backend at `www.asmrgay.com`, with search by
name and by subtitle. Two ways to run:

- **Static site** (GitHub Pages) — no server. Data is pre-exported JSON; audio
  streams directly from the origin.
- **Dynamic server** (`api/server.py`) — live browse fallback + byte proxy, for
  local use.

## Files

| Path | Role |
|---|---|
| `common.py` | AList client (browser UA, retry/backoff), origin URL builder |
| `crawler/crawl.py` | Crawl tree → `graph.sqlite`; parse/index sidecar subtitles |
| `export.py` | `graph.sqlite` → `web/data/{catalog,subs}.json` |
| `update.py` | Crawl **then** export (one command) |
| `web/index.html` | Static site (client search/browse/play) |
| `web/dynamic.html` | Same UI against the live API server |
| `api/server.py` | Optional dynamic server (search/browse/resolve/stream/stats) |

## Update the data (folder structure + media links + subtitles)

```
python update.py                 # crawl all mounts, then export JSON
python update.py --mount asmr     # single mount
python update.py --max-dirs 60    # bounded (fast demo)
```

Outputs `web/data/catalog.json` (folder tree + file list) and
`web/data/subs.json` (subtitle search index). Commit both to publish.

## Run the static site locally

```
python -m http.server 8901 --directory web
# open http://localhost:8901/index.html
```

## Deploy to GitHub Pages

1. Commit the repo (including `web/data/*.json`). `graph.sqlite` is gitignored.
2. Repo Settings → Pages → Source = **Deploy from branch**, folder = **/web**
   (or move `web/` contents to `/docs` and pick `/docs`).
3. Site serves `web/index.html`.

### Why playback works with no proxy

The file origin (`asmr.121231234.xyz`) does **Referer** hotlink protection:
a foreign `Referer` (e.g. `*.github.io`) gets **403**, but an **empty** Referer
returns **206**. The page sets `<meta name="referrer" content="no-referrer">`,
so the browser omits `Referer` and audio plays directly — no server needed.

If the origin later blocks empty Referer or removes `CORS: *`, add a proxy
(e.g. a Cloudflare Worker) that spoofs an allowed Referer, and point the player
at it.

## Notes / limits

- Static search is client-side substring match (works for any length, incl. CJK
  `8月`); it loads the full file list + subtitle index into memory.
- Content is adult + likely pirated — keep this private; do not publish a public
  mirror (see `PLAN.md`).
- Backend is flaky (some dirs hang / 500). The crawler retries; re-run
  `update.py` to fill gaps incrementally.
