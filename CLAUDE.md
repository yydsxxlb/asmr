# ASMR Mirror — operations

Personal catalog + player over the AList backend at `www.asmrgay.com`.
Content streams from origin `asmr.121231234.xyz`. Published **publicly** at
`yydsxxlb.github.io/asmr/` (owner's explicit choice — the site exposes the full
catalog + origin stream URLs).

## Audio sync pipeline (`sync.py`)

Downloads each catalog audio file, transcodes to **Opus 48 kHz VBR 48k**, deletes
the source, keeps only the `.opus` under `web/media/`. Parallel, resumable.

State lives on `nodes` in `graph.sqlite`:
`sync_state` ∈ `unsynced | failed | finished`, plus `opus_path`, `opus_size`.

### Resume / start the sync

```bash
cd <repo>
python sync.py --dl 16 --enc 20 > sync_full.log 2>&1 &
```

- **Resumable**: on start it runs `reconcile` (marks any on-disk `.opus` as
  finished), then skips `finished` and processes `unsynced`/`failed`.
- Safe to kill anytime — only in-flight downloads are lost (staging auto-deleted),
  retried next run.
- Progress: `tail -f sync_full.log`. Lines read `[done/total] ok= fail= <GB> f/s ETA`.
- Bottleneck is download bandwidth (~1 Gbit line), not CPU. Opus has no GPU encoder.

### Pause the sync (keep the web server up)

Kill only the `sync.py` python process (leave `serve.py` running):

```bash
# PowerShell: find + kill sync by command line
Get-CimInstance Win32_Process -Filter "Name='python3.13.exe'" |
  ? { $_.CommandLine -match 'sync\.py' } | % { Stop-Process -Id $_.ProcessId -Force }
```

### Known failures (expected)

- `asmr2` mount → origin returns **403** (blocked). ~1,646 files stay `failed`.
- `__MACOSX*` / resource-fork junk entries → ffmpeg "Invalid data". Not real audio.

## Web server (`serve.py`) — MUST use this, not `http.server`

`serve.py` supports **HTTP Range** (206). Stock `python -m http.server` does NOT,
which breaks `<audio>` seek and shows wrong (few-seconds) duration.

```bash
cd <repo>
python serve.py --port 8901 > httpd.log 2>&1 &      # http://127.0.0.1:8901
```

## Crawl the catalog (`crawler/crawl.py`)

BFS-lists the AList tree into `nodes` in `graph.sqlite` and indexes names +
sidecar subtitles into FTS5. Resumable + incremental (skips dirs whose
`modified` is unchanged since the last crawl, tracked in `crawl_state`).

```bash
cd <repo>
python crawler/crawl.py                 # crawl all mounts (incremental)
python crawler/crawl.py --force         # re-list everything (ignore crawl_state)
python crawler/crawl.py --mount asmr    # single mount
python crawler/crawl.py --max-dirs 40   # bounded demo crawl (fast)
python crawler/crawl.py --workers 8     # parallel requests (default 8)
```

- **Prunes deletions**: when a dir is listed, DB children absent from the fresh
  listing (and their whole subtree) are removed from `nodes`/`files_fts`/`subs`/
  `subs_fts`/`crawl_state`. Only on a **non-empty** listing (a flaky empty 200
  must not wipe a folder). Final line reports `pruned=N`.
- Deletions self-detect: a removed file bumps its parent's `modified` → the dir
  re-lists → stale child pruned. Ghosts that predate this feature need one
  `--force` sweep to clear.
- Empty mounts `asmr3`/`asmr5` → backend 500 (`SharePoint Online` tenant has no
  SPO license). Nothing to crawl until the AList admin fixes the tenant.

Subtitles/lyrics (`.srt/.vtt/.ass/.lrc`): the crawl stores stripped `subs.text`
(for search) **and** timed `subs.cues` (for the synced overlay). To backfill cues
for sub rows crawled before this feature (re-fetches only the sub files, no BFS):

```bash
python crawler/backfill_cues.py         # fill missing cues
python crawler/backfill_cues.py --all   # re-parse every sub row
```

After a crawl, always re-run `export.py` to rebuild the site data.

## Refresh the site data (`export.py`)

Regenerates the **sharded** static catalog under `web/data/` from `graph.sqlite`
(GitHub-Pages ready — no monolithic `catalog.json` anymore):

- `index.json` — mounts + counts + `file_origin` + `generated` (tiny, instant paint).
- `tree/root.json`, `tree/<id>.json` — one shard per directory = its children;
  dir children carry their own shard `id`, fetched lazily on expand.
- `names.json` — flat search index of **playable** media paths `[path, ...]`
  (audio/hls/video); lazy-loaded on first search.
- `subs.json` — subtitle search index; lazy-loaded on first search.
- `lyrics.json` — `{media: [[seconds, line], ...]}` timed cues (from `subs.cues`);
  lazy-loaded on first play. Drives the draggable subtitle overlay (only shows
  when the current track has cues; default on, hide → reopen button bottom-right).
- Dir entries also carry `n`/`sz` = recursive descendant file count / total bytes
  (shown per folder on the site).

Ships the **full tree** (all dirs + all leaves, every kind). **Everything streams
from `file_origin`** — there is no local media anymore (`web/media/` deleted, and
the opus/`.opus` local-playback path was removed from `export.py`/`index.html`).

```bash
python export.py        # re-run after more files finish, then reload the page
```

`web/sw.js` is a stale-while-revalidate service worker (instant repeat visits +
offline browse). Bump its `VERSION` const when the data layout changes.

## Git / deploy

Git repo initialized; public remote **github.com/yydsxxlb/asmr**, served at
**yydsxxlb.github.io/asmr/** via GitHub Actions (`.github/workflows/pages.yml`
uploads `web/`). Push to `main` → auto-deploy.

Heavy/dirty files stay untracked (`.gitignore`): `graph.sqlite`, `web/media/`,
`downloads/`, `sync_full.log`, `httpd.log`, `_dl_list.json`, `__pycache__/`.
The `web/data/` shards **are** committed (that's the site).

```bash
python export.py
git add -A && git commit -m "refresh catalog" && git push   # triggers deploy
```

## Next iteration

Videos / HLS (`.m3u8`, `kind='hls'`) — not synced yet. GPU (NVENC) applies there.
