# Uke player

Phone-first web player for my own ukulele song library: chords over lyrics, riff tabs, auto-scroll,
transposition (low-G tuning aware), keep-screen-on. Source of truth = the **Song Code** field in Notion.

**Keep this repo and its hosting private. `songs.json` contains full lyrics.**

## Files
- `index.html` – the whole app (no dependencies). Reads `songs.json`.
- `build_songs.py` – songs_raw.json -> songs.json, with every transposition (-6..+6) pre-computed.
- `sync_notion.py` – Notion -> songs_raw.json (needs `NOTION_TOKEN`).
- `tools/` – Song Code parser and transposer (chords + 4-string tab riffs).
- `songs_raw.json` – raw Song Code, git-ignored (a demo song is the only content until you sync).

## Sync (on request)
    export NOTION_TOKEN=secret_xxx
    python3 sync_notion.py --exclude "The Suburbs"
    python3 build_songs.py
    git add songs.json && git commit -m "sync songs" && git push

## Run locally
    python3 -m http.server 8000     # then open http://localhost:8000

## Player controls
Tap the text (or space) = start/pause scroll. − / + = scroll speed. ♭ / ♯ = transpose (tap the number to reset).
A− / A+ = font size. ☀ = keep screen awake. Settings are remembered per song on each device.
Riff tabs that can't be moved cleanly at a shift show a ⚠ banner (see the transposer rules).

## Hosting (private)
GitHub Pages on a free plan is public – don't use it for `songs.json`.
Plan: Cloudflare Pages (connect this private repo, no build command, output dir `/`) + Cloudflare Access
(Zero Trust, email one-time PIN, only my address).
