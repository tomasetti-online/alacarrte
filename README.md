# ALACarrte

Turn YouTube music into hi-fi audio. Give it a YouTube video, album/playlist, or channel and it downloads lossless **ALAC / FLAC**, tags the files from MusicBrainz, cuts out sponsored segments, and can push them straight to your Plex music library.

ALACarrte is a self-hosted Flask app. You run it on your own server; your browser and a companion Chrome extension drive it.

> YouTube → hi-fi audio (ALAC / FLAC) · MusicBrainz tagging · SponsorBlock cut · Plex auto-send

## Features

- **Videos, albums/playlists, whole channels** — detect and download the full track list with album structure.
- **True lossless** — transcodes to ALAC (or FLAC) via ffmpeg, not lossy re-encodes.
- **MusicBrainz enrichment** — matches and tags artist / album / title / track numbers so the files land in your library cleanly.
- **SponsorBlock** — cuts sponsored segments during the transcode (yt-dlp's `--sponsorblock-remove all`).
- **Cookies support** — upload your YouTube cookies for age-restricted or logged-in downloads.
- **Notifications** — optional Gotify and Home Assistant push on completion (including a per-job "done" ping).
- **Plex auto-send** — copies finished albums straight into a watched Plex music directory (admin-IP gated).
- **Rate limiting + optional VPN proxy** — built-in per-IP rate limiting, plus an optional gluetun/VPN proxy hop to dodge 429s.
- **Web UI + Chrome extension** — queue and monitor in the browser, or send the page you're on with one click from the extension.

## Quick start (Docker Compose)

Clone and bring it up:

```bash
git clone https://github.com/tomasetti-online/alacarrte.git
cd alacarrte
docker compose up -d
```

Then open <http://localhost:8080>.

That's it — no published image needed; the compose file builds from source. Downloads are stored under `./data` (mounted volume). Everything is off by default except the core downloader, so it works with zero configuration.

## Run without Docker

Requires Python 3.9+, `yt-dlp`, and `ffmpeg` (plus `node` if you want the SponsorBlock cut).

```bash
pip install -r requirements.txt
export ALACARTTE_DATA="$PWD/data"
flask --app app run --port 8080     # or: python -m flask ...
```

The Flask dev server is fine for a single user; for production use `gunicorn` (see the Dockerfile's command).

## Configuration

All configuration is via environment variables. Everything is optional.

| Variable | Default | Purpose |
|---|---|---|
| `ALACARTTE_SECRET_KEY` | random per boot | Flask session signing key. Set a stable value if you run multiple workers. |
| `ALACARTTE_DATA` | `/data` | Where downloaded albums are stored. |
| `ALACARTTE_CONCURRENCY` | `3` | Max parallel downloads. |
| `ALACARTTE_SLEEP_REQUESTS` | `3.0` | Seconds to sleep between yt-dlp requests (politeness / anti-429). |
| `ALACARTTE_RATE_LIMIT` | `60` | Max non-GET API requests per minute per public IP. |
| `ALACARTTE_DL_COOLDOWN` | `1` | Min seconds between `/api/download` calls from the same IP. |
| `ALACARTTE_GOTIFY_URL` | *(disabled)* | Gotify server base, e.g. `https://gotify.example.com/message`. Enables completion notifications. |
| `ALACARTTE_GOTIFY_TOKEN` | *(disabled)* | Gotify app token. |
| `ALACARTTE_HA_WEBHOOK` | *(disabled)* | Home Assistant webhook URL for completion notifications. |
| `ALACARTTE_PLEX_DIR` | *(disabled)* | A Plex-watched directory (mounted). When set, finished albums are copied there. |
| `ALACARTTE_ADMIN_IPS` | *(none)* | Comma-separated IPs allowed to trigger Plex auto-send / admin actions. |
| `ALACARTTE_VPN_PROXY` | *(disabled)* | HTTP proxy URL (e.g. a gluetun container) used for downloads when rate-limited. |
| `ALACARTTE_VPN_CONTROL` | *(disabled)* | Optional VPN switch script/URL to rotate the proxy on 429. |
| `ALACARTTE_AD_SCRIPT` | *(disabled)* | Optional HTML injected on pages (don't ship ad scripts you don't own). |

A worked example with Plex + notifications + VPN is in [`docker-compose.macpro.yml`](docker-compose.macpro.yml) (lab deployment example).

## Chrome extension

The companion extension sends the current YouTube page (or whole channel) to your instance and can export your YouTube cookies.

1. Open `chrome://extensions`, enable **Developer mode**.
2. **Load unpacked** → select the `extension/` folder.
3. Right-click the extension → **Options** → set your ALACarrte instance URL (default `http://localhost:8080`). If your instance isn't localhost, the extension will ask permission to reach it on first use.

See [`extension/README.md`](extension/README.md) for details.

## How it works

1. You (or the extension) POST a URL to `/api/info` (or `/api/channel`).
2. ALACarrte asks yt-dlp what's there, resolves albums/channels into individual tracks.
3. Each track downloads losslessly, is transcoded to ALAC/FLAC with ffmpeg (SponsorBlock cut applied), then tagged from MusicBrainz.
4. Finished albums appear in the UI; optionally copied into your Plex library and announced via Gotify / Home Assistant.

Rate limiting is per-IP and LAN addresses (`10.`, `192.168.`, `172.16.`, `127.`, `::1`) bypass it. When the public YouTube API rate-limit trips, ALACarrte can fall back through a VPN proxy to keep going.

## Security notes

- **No built-in authentication.** ALACarrte is meant for a trusted network (or behind a reverse proxy with auth, like the lab's portal gate). Put it behind something that protects it before exposing it to the internet.
- `ALACARTTE_SECRET_KEY` — set it to a random string in production so sessions survive restarts.
- Cookies you upload are stored in your `ALACARTTE_DATA` volume. Keep that volume private.
- MusicBrainz lookups use the repo URL in the User-Agent — keep that accurate if you fork.

## License

[MIT](LICENSE) — Copyright (c) 2026 TJ Tomasetti.

Built on [yt-dlp](https://github.com/yt-dlp/yt-dlp), [Flask](https://flask.palletsprojects.com/), and ffmpeg.
