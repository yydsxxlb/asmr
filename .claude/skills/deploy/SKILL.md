---
name: deploy
description: Build and publish the ASMR site — rebuild static data and push to GitHub Pages (yydsxxlb.github.io/asmr/). Use for "deploy", "publish the site", "push the website", "update the live site".
---

# Deploy the site

Publishes to **github.com/yydsxxlb/asmr** → served at **yydsxxlb.github.io/asmr/**
via GitHub Actions (`.github/workflows/pages.yml` uploads `web/`). Run from the
repo root.

## Steps

1. Rebuild the static data (skip if you just ran the **crawl** skill):

   ```bash
   python export.py
   ```

2. If the data layout changed (not just content), bump the service-worker cache
   so clients don't serve stale shards — edit `web/sw.js`, increment
   `const VERSION = 'asmr-vN'`.

3. Commit and push (push to `main` triggers the Pages deploy):

   ```bash
   git add -A
   git commit -m "refresh catalog"
   git push
   ```

4. Watch the deploy finish:

   ```bash
   gh run watch --exit-status
   ```

## Verify live

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://yydsxxlb.github.io/asmr/data/index.json
```

Expect `200`. Do **not** commit heavy/dirty files — `.gitignore` already excludes
`graph.sqlite`, `web/media/`, `downloads/`, logs. The `web/data/` shards ARE the
site and must be committed.
