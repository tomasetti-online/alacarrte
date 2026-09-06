# ALACarrte — Finish-Up Plan (2026-09-06)

**Status: feature-complete BETA, already live.** macpro :3080 (gated `alacarrte.tomasetti.online` via portal-gate), wired into the lab: Lidarr YouTube miss-path (`scripts/lidarr-youtube-fallback.ps1`), Plex auto-send to `F:\Media\Music`, Gotify + HA notifications. This clone (`apps/alacarrte`, vault-adjacent working copy) is now the finish-up surface. Vault decision 2026-09-06: **BUILD, not kill** (resolves the old "unclear-alacarrte-and-clean-sites-data-products-json-tj" todo).

## Priority 1 — Security (do first)
- **Live Gotify token committed in `docker-compose.macpro.yml`** (`ALACARTTE_GOTIFY_TOKEN=real value`) — and this repo is on public GitHub, so treat it as exposed. **Rotate the token in Gotify**, update the live container env, THEN scrub the file to a placeholder. (Repo copy scrubbed 2026-09-06; live macpro compose unaffected — it carries its own env. ROTATION STILL PENDING.) Rotation makes git history harmless; no rewrite needed.

## Priority 2 — Real bugs
- **Zombie retry:** `/api/retry-track/<tid>/<idx>` spawns a second `process_track` without checking task status — retrying inside a still-"downloading" task double-counts GLOBAL_SLOTS and can wedge the queue. Gate retry to `done`/`error` tasks (or per-track done flags).
- **`_RATE_LIMITED` race:** flag flips True/False globally while concurrent downloads read it mid-flight; a reconnect can drop the proxy for in-flight runs. Minor — acceptable for BETA, note it.

## Priority 3 — Polish / consistency
- **Stale repo URLs:** `app.py` MusicBrainz User-Agent + README say `github.com/tjtomasetti/alacarrte`; templates + extension say `github.com/tomasetti-online/alacarrte` (correct one). Unify to `tomasetti-online`.
- **README docker run line** references `tomasetti-online/alacarrte` image — no publish workflow exists in the repo (no `.github/`). Decide: add a GHCR publish Actions workflow, or change the README to build-from-compose.
- **Extension** hardcodes `https://alacarrte.tomasetti.online` in `popup.js` — fine for lab use, needs an options field before any public distribution.

## Priority 4 — Lab integration housekeeping
- `infra/SERVICES.md` row is current (macpro :3080, `/srv/pool/alacarrte-data`, compose at `/srv/docker/alacarrte/`) — keep it true when changes deploy.
- Apex live-dot strip still advertises Alacarrte (`sites/data/products.json`) — with the BUILD decision locked, that stays; the cleanup todo was really about NovaTube + stale entries.
- macpro deploy path: `C:\Users\tjtom\Music\alacarrte\` was the original source pre-vault; push from here going forward, or sync both.

## Where the code stands
- Flask + yt-dlp, ~2,000 lines (app.py 770, 3 templates, chrome extension). Features: video/playlist/channel → ALAC/FLAC, MusicBrainz enrichment, SponsorBlock cut, per-session cookies, queue + concurrency (3), rate-limit backoff, Gluetun VPN proxy switch on 429, Plex send, Gotify/HA notify, zip-all, per-track retry, health endpoint.
- `PY_COMPILE_OK` on 2026-09-06. Tests: none — if we extend, add a pytest file (tubeforge's fake-yt-dlp PATH-shim pattern is the lab precedent).
