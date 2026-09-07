# ALACarrte

> **BETA** — This software is in active development. Use at your own risk.

Self-hosted YouTube to hi-fi audio downloader. Paste a YouTube video, playlist, or artist channel URL and get ALAC or FLAC files with MusicBrainz-enriched metadata, cover art, and SponsorBlock-cut audio.

## Features

- YouTube videos, playlists, and channel album discovery
- ALAC (Apple Lossless) and FLAC output
- MusicBrainz metadata enrichment (album, year, track numbers)
- SponsorBlock integration (removes intros/outros/sponsors)
- Channel mode: paste an artist handle to see all releases with checkbox selection
- Queue system — download multiple albums simultaneously
- Per-track retry for failed downloads
- Cookie support: upload your browser cookies for age-restricted / Premium content
- Plex auto-send (optional, admin IPs only)
- Chrome extension for one-click downloading
- Multi-artist playlist handling (Various Artists organization)
- Rate limiting and concurrent download management

## Quick Start

### Docker (build from source — no published image)

`ash
git clone https://github.com/tomasetti-online/alacarrte.git && cd alacarrte
docker build -t alacarrte .
docker run -d --restart always --name alacarrte -p 8080:8080 -v ./data:/data alacarrte
`

### Docker Compose

`ash
wget https://raw.githubusercontent.com/tomasetti-online/alacarrte/main/docker-compose.yml
docker compose up -d
`

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| ALACARTTE_PORT | 8080 | Web server port |
| ALACARTTE_CONCURRENCY | 6 | Max concurrent track downloads |
| ALACARTTE_SLEEP_REQUESTS | 1.5 | Seconds between YouTube API requests |
| ALACARTTE_RATE_LIMIT | 60 | Max API requests per minute per IP |
| ALACARTTE_DL_COOLDOWN | 1 | Seconds between download submissions per IP |
| ALACARTTE_SECRET_KEY | (random) | Flask session secret key |
| ALACARTTE_ADMIN_IPS | (empty) | Comma-separated IPs for admin features (Plex) |
| ALACARTTE_PLEX_DIR | (empty) | Mount path for Plex music folder |
| ALACARTTE_GOTIFY_URL | (empty) | Gotify server URL for notifications |
| ALACARTTE_GOTIFY_TOKEN | (empty) | Gotify app token |
| ALACARTTE_HA_WEBHOOK | (empty) | Home Assistant webhook URL |
| ALACARTTE_AD_SCRIPT | (empty) | Ad network embed script |
| ALACARTTE_DATA | /data | Data directory for downloads |

## Cookies

To download age-restricted or YouTube Music Premium content, export cookies from your browser:

`ash
yt-dlp --cookies-from-browser firefox --cookies cookies.txt
`

Then mount the file or upload via the Settings page.

## Disclaimer

ALACarrte is provided as-is, in BETA. You are solely responsible for the content you download and how you use it. Respect copyright laws and platform terms of service. This tool is for personal, non-commercial use. No warranty or liability is assumed.

## License

MIT
