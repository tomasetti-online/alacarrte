# ALACarrte — Finish-Up Plan (2026-09-06)

**Status: feature-complete BETA, already live. P1 DONE + verified 2026-09-06; P2 fixed in e074e5d; P3 mostly done.**

## Status log

- **2026-09-06 - P1 GOTIFY TOKEN ROTATION: DONE + VERIFIED.** Rotated in Gotify (Homelab app -> id 5; old id 3 deleted after cutover; id 4 was created then accidentally deleted during rotation - brief fleet push outage). Live macpro container already runs the NEW token (`<GOTIFY-TOKEN-SCRUBBED>...`). Verified end-to-end this session: test push with new token = HTTP 200; leaked old token = HTTP 401 (dead). Vault cleanups that landed: `secrets/lab-credentials.json` homelab token updated; `infra/CREDENTIALS.md` regenerated (was stale - see bug note below); dead-code hardcodes in `apps/kanboard-gotify-bridge/bridge.py` + `scripts/health-monitor.py` swapped to the new token (both legacy/undeployed - nothing live consumed them).
- **2026-09-06 - vault generator bug found + fixed:** `scripts/generate-credentials-md.py` `replace_section` read `match.group(1)` (full marker string) instead of `match.group(2)` (section name) -> NO section was ever regenerated and `--check` was a tautology. Fixed + regenerated; gotify table now shows the new token. LESSONS-LEARNED entry added. macpro :3080 (gated `alacarrte.tomasetti.online` via portal-gate), wired into the lab: Lidarr YouTube miss-path (`scripts/lidarr-youtube-fallback.ps1`), Plex auto-send to `F:\Media\Music`, Gotify + HA notifications. This clone (`apps/alacarrte`, vault-adjacent working copy) is now the finish-up surface. Vault decision 2026-09-06: **BUILD, not kill** (resolves the old "unclear-alacarrte-and-clean-sites-data-products-json-tj" todo).

## Priority 1 — Security (do first)
- **Live Gotify token committed in `docker-compose.macpro.yml`** — **DONE 2026-09-06**: rotated in Gotify, live container repointed + verified (200 with new / 401 with leaked), repo copy scrubbed. The old token in public git history is dead — no rewrite needed. Vault sweep: nothing left referencing the old prefix `<GOTIFY-TOKEN-SCRUBBED>...` except git history.

## Priority 2 — Real bugs
- **Zombie retry:** **FIXED in e074e5d** (retry gated to finished/failed tracks under LOCK; verified in code — repo tree clean, py_compile OK).
- **`_RATE_LIMITED` race:** flag flips True/False globally while concurrent downloads read it mid-flight; a reconnect can drop the proxy for in-flight runs. Minor — acceptable for BETA, note it.

## Priority 3 — Polish / consistency
- **Stale repo URLs:** **DONE** (e074e5d) — all refs point to `tomasetti-online/alacarrte` (app.py UA, templates, README).
- **README docker run line:** **FIXED 2026-09-06** — now build-from-source (`git clone` -> `docker build -t alacarrte .` -> `docker run ... alacarrte`). **Decision:** NO GHCR publish workflow — the lab deploys via build-from-repo (`deploy-remote.sh` on macpro); publishing is deferred until there is a reason to distribute the image.
- **Extension** hardcodes `https://alacarrte.tomasetti.online` in `popup.js` — fine for lab use, needs an options field before any public distribution.

## Priority 4 — Lab integration housekeeping
- `infra/SERVICES.md` row is current (macpro :3080, `/srv/pool/alacarrte-data`, compose at `/srv/docker/alacarrte/`) — keep it true when changes deploy.
- Apex live-dot strip still advertises Alacarrte (`sites/data/products.json`) — with the BUILD decision locked, that stays; the cleanup todo was really about NovaTube + stale entries.
- macpro deploy path: `C:\Users\tjtom\Music\alacarrte\` was the original source pre-vault; push from here going forward, or sync both.

## Where the code stands
- Flask + yt-dlp, ~2,000 lines (app.py 770, 3 templates, chrome extension). Features: video/playlist/channel → ALAC/FLAC, MusicBrainz enrichment, SponsorBlock cut, per-session cookies, queue + concurrency (3), rate-limit backoff, Gluetun VPN proxy switch on 429, Plex send, Gotify/HA notify, zip-all, per-track retry, health endpoint.
- `PY_COMPILE_OK` on 2026-09-06. Tests: none — if we extend, add a pytest file (tubeforge's fake-yt-dlp PATH-shim pattern is the lab precedent).
