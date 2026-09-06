---
name: deploy
description: Build and publish the ASMR site — rebuild static data and push to GitHub Pages (yydsxxlb.github.io/asmr/). Use for "deploy", "publish the site", "push the website", "update the live site".
---

# Deploy the site

Publishes to **github.com/yydsxxlb/asmr** → served at **yydsxxlb.github.io/asmr/**
via GitHub Actions (`.github/workflows/pages.yml` uploads `web/`). Run from the
repo root. The `yydsxxlb` remote is `yyds` (not `origin`).

## Steps

0. Make `yydsxxlb` the active GitHub account + neutral commit identity (keeps the
   owner's personal name/email out of the public repo):

   ```bash
   gh auth switch --user yydsxxlb
   git config user.name  "yydsxxlb"
   git config user.email "325523986+yydsxxlb@users.noreply.github.com"
   ```

1. Rebuild the static data (skip if you just ran the **crawl** skill):

   ```bash
   python export.py
   ```

2. If the data layout changed (not just content), bump the service-worker cache
   so clients don't serve stale shards — edit `web/sw.js`, increment
   `const VERSION = 'asmr-vN'`.

3. Commit and push to the `yyds` remote, pinned to the yydsxxlb token so no other
   account's credentials are used (push to `main` triggers the Pages deploy):

   ```bash
   git add -A
   git commit -m "refresh catalog"
   TOKEN=$(gh auth token)   # active account = yydsxxlb
   git push "https://x-access-token:${TOKEN}@github.com/yydsxxlb/asmr.git" main:main
   ```

4. Watch the deploy finish:

   ```bash
   gh run watch -R yydsxxlb/asmr --exit-status
   ```

## Verify live

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://yydsxxlb.github.io/asmr/data/index.json
```

Expect `200`. Do **not** commit heavy/dirty files — `.gitignore` already excludes
`graph.sqlite`, `web/media/`, `downloads/`, logs. The `web/data/` shards ARE the
site and must be committed.
