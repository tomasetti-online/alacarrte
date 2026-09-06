import os, json, uuid, threading, shutil, zipfile, time, subprocess, re, signal, urllib.request, urllib.parse, datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from flask import Flask, render_template, request, jsonify, send_file, send_from_directory, abort, session

app = Flask(__name__)
app.secret_key = os.environ.get("ALACARTTE_SECRET_KEY", os.urandom(24).hex())

DATA_DIR = os.environ.get("ALACARTTE_DATA", "/data")
os.makedirs(DATA_DIR, exist_ok=True)

# All configuration via env vars with sensible defaults
GOTIFY_URL = os.environ.get("ALACARTTE_GOTIFY_URL", "")
GOTIFY_TOKEN = os.environ.get("ALACARTTE_GOTIFY_TOKEN", "")
PLEX_DIR = os.environ.get("ALACARTTE_PLEX_DIR", "")
HA_WEBHOOK = os.environ.get("ALACARTTE_HA_WEBHOOK", "")
ADMIN_IPS = set(os.environ.get("ALACARTTE_ADMIN_IPS", "").replace(" ", "").split(",")) - {""}
CONCURRENCY = int(os.environ.get("ALACARTTE_CONCURRENCY", "3"))
SLEEP_REQUESTS = os.environ.get("ALACARTTE_SLEEP_REQUESTS", "3.0")
RATE_LIMIT = int(os.environ.get("ALACARTTE_RATE_LIMIT", "60"))
DL_COOLDOWN = int(os.environ.get("ALACARTTE_DL_COOLDOWN", "1"))
AD_SCRIPT = os.environ.get("ALACARTTE_AD_SCRIPT", "")
VPN_PROXY = os.environ.get("ALACARTTE_VPN_PROXY", "")  # e.g. http://10.0.0.3:18888
VPN_CONTROL = os.environ.get("ALACARTTE_VPN_CONTROL", "")  # script path or API URL

tasks = {}
LOCK = threading.Lock()
CLEANUP_INTERVAL = 600

GLOBAL_SLOTS = threading.Semaphore(CONCURRENCY)
rate_map = {}

def bg_cleanup():
    while True:
        time.sleep(CLEANUP_INTERVAL)
        now = time.time()
        with LOCK:
            dead = [tid for tid, t in tasks.items()
                    if t["status"] in ("done", "error")
                    and now - t["_ts"] > 604800]  # 7 days
            for tid in dead:
                shutil.rmtree(tasks[tid]["_dir"], ignore_errors=True)
                del tasks[tid]

threading.Thread(target=bg_cleanup, daemon=True).start()

def run_ytdl(args, timeout=180, task=None):
    cmd = ["yt-dlp", "--no-warnings", "--no-progress"] + args
    # Session-based cookies first, then fall back to global cookies file
    sid = "default"
    if task:
        sid = task.get("_sid", "default")
    else:
        try:
            sid = session.get("sid", "default")
        except RuntimeError:
            sid = "default"
    cookie_dir = os.path.join(DATA_DIR, "cookies")
    sc = os.path.join(cookie_dir, f"{sid}.txt")
    gc = os.path.join(DATA_DIR, "cookies.txt")
    if os.path.exists(sc):
        cmd = cmd[:1] + ["--cookies", sc] + cmd[1:]
    elif os.path.exists(gc):
        cmd = cmd[:1] + ["--cookies", gc] + cmd[1:]
    if VPN_PROXY and _RATE_LIMITED:
        cmd = cmd[:1] + ["--proxy", VPN_PROXY] + cmd[1:]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    # Check for rate limiting in output and trigger VPN switch
    if VPN_PROXY and (r.returncode != 0) and ("rate-limited" in r.stderr.lower() or "429" in r.stderr or "too many requests" in r.stderr.lower()):
        threading.Thread(target=vpn_reconnect, daemon=True).start()
    return r.returncode, r.stdout, r.stderr

_MB_LAST = 0

def search_musicbrainz(artist, title):
    """Look up track metadata from MusicBrainz. Returns dict with album, year, track, total, or None."""
    global _MB_LAST
    now = time.time()
    if now - _MB_LAST < 1.2:
        time.sleep(1.2 - (now - _MB_LAST))
    query = urllib.parse.quote(f'artist:"{artist}" AND recording:"{title}"')
    url = f"https://musicbrainz.org/ws/2/recording/?query={query}&fmt=json&limit=5"
    req = urllib.request.Request(url, headers={"User-Agent": "ALACarrte/1.0 ( github.com/tomasetti-online/alacarrte )"})
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
    except Exception:
        return None
    finally:
        _MB_LAST = time.time()

    if not data.get("recordings"):
        return None

    for rec in data["recordings"]:
        if not rec.get("releases"):
            continue
        for release in rec["releases"]:
            album = release.get("title", "")
            year = release.get("date", "")[:4] if release.get("date") else ""
            track_num = None
            total_tracks = None

            for media in release.get("media", []):
                for track in media.get("tracks", []):
                    if track.get("title", "").lower().strip() == title.lower().strip():
                        track_num = track.get("position")
                        total_tracks = media.get("track-count") or media.get("track_count")
                        break
            # If we didn't find exact match in tracks, use position from recording
            if track_num is None:
                # Try to find the position from the recording in releases
                for media in release.get("media", []):
                    for track in media.get("tracks", []):
                        rt = track.get("recording", {})
                        if rt.get("id") == rec.get("id"):
                            track_num = track.get("position")
                            total_tracks = media.get("track-count") or media.get("track_count")
                            break

            return dict(album=album, year=year,
                        track=track_num, total=total_tracks,
                        source="MusicBrainz")
    return None

def slugify(s):
    s = re.sub(r'[\\/:*?"<>|]', '', s).strip()
    return s[:120]

def is_admin(ip):
    return ip in ADMIN_IPS

@app.before_request
def ensure_session():
    if not session.get("sid"):
        session["sid"] = uuid.uuid4().hex[:12]

@app.route("/settings")
def settings_page():
    return render_template("settings.html")

@app.route("/api/cookies", methods=["GET", "POST", "DELETE"])
def api_cookies():
    sid = session.get("sid", "default")
    cookie_dir = os.path.join(DATA_DIR, "cookies")
    os.makedirs(cookie_dir, exist_ok=True)
    path = os.path.join(cookie_dir, f"{sid}.txt")
    if request.method == "GET":
        exists = os.path.exists(path)
        size = os.path.getsize(path) if exists else 0
        return jsonify(exists=exists, size=size)
    elif request.method == "POST":
        content = request.form.get("content", "")
        if not content:
            return jsonify(error="No content provided"), 400
        # Validate it looks like a cookies file
        if not content.startswith("# Netscape HTTP Cookie File") and not content.startswith("# HTTP Cookie File"):
            return jsonify(error="Invalid cookie format. Use yt-dlp --cookies-from-browser to export."), 400
        with open(path, "w") as f:
            f.write(content)
        return jsonify(status="saved", size=os.path.getsize(path))
    elif request.method == "DELETE":
        if os.path.exists(path):
            os.remove(path)
        return jsonify(status="deleted")
    return jsonify(error="Invalid method"), 405

def check_rate(ip, action="request", max_per_min=RATE_LIMIT, cooldown=0):
    """Rate limit per IP. Returns (allowed: bool, wait_seconds: int)."""
    if ip.startswith(("10.", "192.168.", "172.16.", "127.", "::1")) or ip in ADMIN_IPS:
        return True, 0
    now = time.monotonic()
    with LOCK:
        entries = rate_map.get(ip, [])
        entries = [t for t in entries if now - t < 60]
        if len(entries) >= max_per_min:
            return False, max(1, int(60 - (now - entries[0])))
        if cooldown and entries and now - entries[-1] < cooldown:
            return False, max(1, int(cooldown - (now - entries[-1])))
        entries.append(now)
        rate_map[ip] = entries
    return True, 0

@app.before_request
def rate_limit_middleware():
    ip = request.remote_addr or "unknown"
    if request.path.startswith("/api/download"):
        allowed, wait = check_rate(ip, cooldown=DL_COOLDOWN)
        if not allowed:
            return jsonify(error=f"Too many downloads. Wait {wait}s."), 429
    elif request.path.startswith("/api/") and request.method != "GET":
        allowed, wait = check_rate(ip)
        if not allowed:
            return jsonify(error=f"Rate limited. Wait {wait}s."), 429

def clean_title(title):
    title = re.sub(r'(?i)\s*\(Official\s+Music\s+Video\)', '', title)
    title = re.sub(r'(?i)\s*\(Official\s+Video\)', '', title)
    title = re.sub(r'(?i)\s*\(Official\s+Audio\)', '', title)
    title = re.sub(r'(?i)\s*\(Music\s+Video\)', '', title)
    title = re.sub(r'(?i)\s*\(Lyric[s]?\s+Video\)', '', title)
    title = re.sub(r'(?i)\s*\[Official\s+Music\s+Video\]', '', title)
    title = re.sub(r'(?i)\s*\[Official\s+Video\]', '', title)
    title = re.sub(r'(?i)\s*\[Video\]', '', title)
    title = re.sub(r'(?i)\s*\[Audio\]', '', title)
    title = re.sub(r'(?i)\s*with\s+(lyrics|download)\s+link.*$', '', title)
    title = re.sub(r'(?i)\s*Official\s+Lyrics?\s*(Video)?$', '', title)
    title = re.sub(r'\s+', ' ', title).strip()
    title = re.sub(r'[\s\-\.\?,;:]+$', '', title).strip()
    return title

@app.route("/")
def index():
    return render_template("index.html", ad_script=AD_SCRIPT)

@app.route("/api/info", methods=["POST"])
def api_info():
    url = request.form.get("url", "").strip()
    if not url:
        return jsonify(error="Enter a URL"), 400
    code, out, err = run_ytdl(["--dump-single-json", "--flat-playlist", url])
    if code != 0:
        return jsonify(error=err.strip() or "Failed to fetch info"), 400
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return jsonify(error="Could not parse response"), 500

    playlist_title = None
    entries = []
    if data.get("_type") == "playlist":
        playlist_title = data.get("title", "Unknown Album")
        entries = data.get("entries", [])
    else:
        entries = [data]

    songs = []
    for e in entries:
        vid = e.get("id")
        raw = e.get("title", "Unknown")
        uploader = e.get("uploader", "")
        parsed_artist, parsed_title = "Unknown Artist", raw
        if " - " in raw:
            parts = raw.split(" - ", 1)
            parsed_artist = parts[0].strip()
            parsed_title = parts[1].strip()
        elif uploader:
            parsed_artist = uploader
        songs.append(dict(
            id=vid, title=parsed_title, artist=parsed_artist,
            uploader=uploader,
        ))

    return jsonify(playlist=playlist_title, tracks=songs)

def is_channel_url(url):
    # Strip trailing /playlists, /videos, /releases, /shorts
    clean = re.sub(r'/(playlists|videos|releases|shorts)/?$', '', url.rstrip('/'))
    return bool(re.search(r'youtube\.com/(@[^/]+|channel/[^/]+|c/[^/]+)$', clean))

@app.route("/api/channel", methods=["POST"])
def api_channel():
    url = request.form.get("url", "").strip()
    if not url:
        return jsonify(error="Enter a channel URL"), 400

    # Extract channel handle or ID from URL
    channel_handle = None
    channel_id = None
    m_handle = re.search(r'youtube\.com/@([^/]+)', url)
    m_cid = re.search(r'youtube\.com/channel/([^/]+)', url)
    if m_handle:
        channel_handle = m_handle.group(1)
    elif m_cid:
        channel_id = m_cid.group(1)
    else:
        return jsonify(error="Could not parse channel URL"), 400

    # If we have a handle, find the channel ID via search
    if channel_handle and not channel_id:
        handle_clean = channel_handle.replace("-", " ")
        code, out, _ = run_ytdl(["--flat-playlist", "--dump-single-json", "--playlist-end", "1",
                                  f"ytsearch1:{handle_clean}"])
        if code != 0:
            return jsonify(error="Failed to find channel"), 400
        try:
            sdata = json.loads(out)
            vid = sdata.get("entries", [{}])[0]
            channel_id = vid.get("channel_id", "")
        except Exception:
            pass

    if not channel_id:
        return jsonify(error="Could not find channel ID"), 400

    # Fetch releases from the channel
    releases_url = f"https://www.youtube.com/channel/{channel_id}/releases"
    code, out, err = run_ytdl(["--flat-playlist", "--dump-single-json", releases_url])
    if code != 0:
        return jsonify(error=err.strip() or "Failed to fetch releases"), 400
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return jsonify(error="Could not parse releases"), 500

    channel_title = data.get("title", "")
    entries = data.get("entries", [])

    releases = []
    for e in entries:
        eid = e.get("id", "")
        title = e.get("title", "Untitled")
        pc = e.get("playlist_count", 0) or 0
        if eid.startswith("OLAK"):
            releases.append(dict(id=eid, title=title, count=pc if pc > 0 else "?"))

    return jsonify(
        channel=channel_title,
        artist=channel_title.replace(" - Topic", "").replace(" - Videos", "").replace(" - Releases", "").strip(),
        releases=releases,
    )

def do_download(tid, url, folder):
    t = tasks[tid]
    t["status"] = "downloading"
    raw_dir = os.path.join(folder, "raw")
    alac_dir = os.path.join(folder, "alac")
    os.makedirs(raw_dir, exist_ok=True)
    os.makedirs(alac_dir, exist_ok=True)

    total = len(t["tracks"])
    cover_path = None

    # Download cover thumbnail once from the first track
    first_vid = t["tracks"][0]["id"]
    thumb_dir = os.path.join(raw_dir, "thumbs")
    os.makedirs(thumb_dir, exist_ok=True)
    cover_code, _, _ = run_ytdl([
        "--write-thumbnail", "--convert-thumbnails", "jpg",
        "--skip-download", "--no-playlist",
        "-o", os.path.join(thumb_dir, "cover"),
        f"https://www.youtube.com/watch?v={first_vid}",
    ], timeout=30, task=t)
    if cover_code == 0:
        candidates = sorted(os.listdir(thumb_dir))
        jpgs = [f for f in candidates if f.lower().endswith((".jpg", ".jpeg"))]
        if jpgs:
            best = max(jpgs, key=lambda f: os.path.getsize(os.path.join(thumb_dir, f)))
            cover_path = os.path.join(thumb_dir, best)

    done_count = [0]
    dl_lock = threading.Lock()
    launched = []
    finished = [False] * total

    def run_one(idx):
        try:
            process_track(t, idx, raw_dir, alac_dir, cover_path)
        finally:
            with dl_lock:
                done_count[0] += 1
                finished[idx] = True
                t["progress"] = (done_count[0], total, t["tracks"][idx].get("title", ""))

    def wait_for_slot_and_run(idx):
        run_one(idx)

    for idx in range(total):
        if not t["tracks"][idx].get("done"):
            th = threading.Thread(target=wait_for_slot_and_run, args=(idx,), daemon=True)
            th.start()
            launched.append(th)

    # Keep waiting until all tracks are actually done, with periodic checks
    last_progress = time.time()
    last_done = 0
    while True:
        with dl_lock:
            all_done = all(t["tracks"][i].get("done", False) for i in range(total))
            current_done = sum(1 for i in range(total) if t["tracks"][i].get("done", False))
        if all_done:
            break
        # If no progress for 10 minutes, fail remaining tracks
        if current_done > 0 and current_done == last_done:
            if time.time() - last_progress > 600:
                break
        else:
            last_progress = time.time()
            last_done = current_done
        time.sleep(5)
    # Mark any stuck tracks as failed
    for i in range(total):
        if not t["tracks"][i].get("done", False) and not t["tracks"][i].get("error"):
            t["tracks"][i]["error"] = "Timed out waiting for slot"

    t["progress"] = (total, total, "Done")
    t["status"] = "done"
    t["_ts"] = time.time()

    # Auto-send to Plex if available (only for admin IPs)
    try:
        if PLEX_DIR and os.path.isdir(PLEX_DIR) and t.get("client_ip") in ADMIN_IPS:
            done_tracks = [tr for tr in t["tracks"] if tr.get("done")]
            if done_tracks:
                artists = set(tr.get("artist", "Unknown Artist") for tr in done_tracks)
                album = slugify(t["album"])
                for tr in done_tracks:
                    artist = slugify(tr.get("artist", "Unknown Artist"))
                    sub = "Various Artists" if len(artists) > 1 else artist
                    dest = os.path.join(PLEX_DIR, sub, album)
                    os.makedirs(dest, exist_ok=True)
                    f = os.path.basename(tr["path"])
                    src = tr["path"]
                    dst = os.path.join(dest, f)
                    if os.path.exists(dst): continue
                    shutil.copy2(src, dst)
    except Exception:
        pass

def process_track(t, idx, raw_dir, alac_dir, cover_path):
    track = t["tracks"][idx]
    vid = track["id"]
    artist = track["artist"]
    title = clean_title(track["title"])
    album = t["album"]
    total = len(t["tracks"])
    safe_title = slugify(title)
    out_template = os.path.join(raw_dir, f"{idx+1:02d} - {safe_title}.%(ext)s")

    GLOBAL_SLOTS.acquire()
    try:
        last_err = ""
        for attempt in range(3):
            code, _, err = run_ytdl([
                "-x", "--audio-format", "flac",
                "--sleep-requests", SLEEP_REQUESTS,
                "--extractor-retries", "1",
                "--js-runtimes", "node",
                "--sponsorblock-remove", "all",
                "--no-playlist", "--no-embed-metadata",
                "-o", out_template,
                f"https://www.youtube.com/watch?v={vid}",
            ], timeout=300, task=t)
            if code == 0:
                break
            last_err = err.strip()
            if attempt < 2:
                time.sleep(3)
        if code != 0:
            track["error"] = f"Failed after 3 attempts: {last_err}"
            return

        flac_files = sorted([f for f in os.listdir(raw_dir) if f.endswith(".flac") and f.startswith(f"{idx+1:02d}")])
        if not flac_files:
            track["error"] = "No FLAC produced"
            return

        flac_path = os.path.join(raw_dir, flac_files[0])
        out_name = f"{idx+1:02d} - {safe_title}.{'m4a' if t.get('format','alac') == 'alac' else 'flac'}"
        out_path = os.path.join(alac_dir, out_name)

        # Enrich metadata from MusicBrainz
        mb = search_musicbrainz(artist, title)
        if mb:
            if mb.get("album"): album = mb["album"]
            if mb.get("year"): track["year"] = mb["year"]
            if mb.get("track"): track["mb_track"] = mb["track"]
            if mb.get("total"): track["mb_total"] = mb["total"]

        year = track.get("year", "")
        mb_track = track.get("mb_track", idx+1)
        mb_total = track.get("mb_total", total)

        if t.get("format") == "flac":
            shutil.copy2(flac_path, out_path)
        else:
            ff_args = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y",
                        "-i", flac_path]
            map_flags = ["-map", "0:a"]
            if cover_path and os.path.exists(cover_path):
                ff_args += ["-i", cover_path]
                map_flags += ["-map", "1:v"]
            ff_args += map_flags + [
                "-c:a", "alac"] + (["-c:v", "copy"] if cover_path and os.path.exists(cover_path) else []) + ["-movflags", "+faststart",
                "-metadata", f"title={title}",
                "-metadata", f"artist={artist}",
                "-metadata", f"album_artist={artist}",
                "-metadata", f"album={album}",
                "-metadata", f"track={mb_track}/{mb_total}",
            ] + (["-metadata", f"date={year}"] if year else [])
            if cover_path and os.path.exists(cover_path):
                ff_args += ["-metadata:s:v", "title=Album cover", "-metadata:s:v", "comment=Cover (front)", "-disposition:v", "attached_pic"]
            ff_args += [out_path]
            try:
                r = subprocess.run(ff_args, capture_output=True, text=True, timeout=180)
                if r.returncode != 0:
                    track["error"] = r.stderr.strip() or f"ffmpeg exit {r.returncode}"
                    return
            except Exception as e:
                track["error"] = str(e)
                return

        track["path"] = out_path
        track["done"] = True
        track["title"] = title
        track["artist"] = artist
        os.remove(flac_path)
    finally:
        GLOBAL_SLOTS.release()

@app.route("/api/download", methods=["POST"])
def api_download():
    url = request.form.get("url", "").strip()
    if not url:
        return jsonify(error="Enter a URL"), 400
    album_override = request.form.get("album", "").strip()
    artist_override = request.form.get("artist", "").strip()
    output_format = request.form.get("format", "alac").strip()

    code, out, err = run_ytdl(["--dump-single-json", "--flat-playlist", url])
    if code != 0:
        return jsonify(error=err.strip() or "Failed to fetch info"), 400
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return jsonify(error="Parse error"), 500

    playlist_title = None
    entries = []
    if data.get("_type") == "playlist":
        playlist_title = data.get("title", "Unknown Album")
        entries = data.get("entries", [])
    else:
        entries = [data]

    tid = uuid.uuid4().hex[:12]
    folder = os.path.join(DATA_DIR, tid)
    os.makedirs(folder, exist_ok=True)

    album = album_override or playlist_title or "Downloaded Music"
    songs = []
    for e in entries:
        raw = e.get("title", "Unknown")
        uploader = e.get("uploader", "")
        parsed_artist, parsed_title = "Unknown Artist", raw
        if " - " in raw:
            parts = raw.split(" - ", 1)
            parsed_artist = parts[0].strip()
            parsed_title = parts[1].strip()
        elif uploader:
            parsed_artist = uploader
        songs.append(dict(
            id=e.get("id"), title=parsed_title, artist=parsed_artist,
            uploader=uploader, done=False,
        ))

    t = dict(
        tid=tid, status="info", album=album,
        album_artist=artist_override or None,
        format=output_format,
        client_ip=request.remote_addr,
        _sid=session.get("sid", "default"),
        tracks=songs, progress=(0, len(songs), ""),
        _dir=folder, _ts=time.time(),
    )
    tasks[tid] = t
    threading.Thread(target=do_download, args=(tid, url, folder), daemon=True).start()

    return jsonify(tid=tid, redirect=f"/status/{tid}")

@app.route("/api/status/<tid>")
def api_status(tid):
    t = tasks.get(tid)
    if not t:
        return jsonify(error="Task not found"), 404
    return jsonify(
        status=t["status"], album=t["album"], format=t.get("format", "alac"),
        tracks=[dict(title=tr["title"], artist=tr["artist"],
                     done=tr.get("done", False), error=tr.get("error"),
                     file=os.path.basename(tr["path"]) if tr.get("path") else None)
                for tr in t["tracks"]],
        progress=list(t["progress"]),
    )

@app.route("/status/<tid>")
def status_page(tid):
    t = tasks.get(tid)
    if not t:
        return render_template("index.html", error="Task not found")
    return render_template("status.html", tid=tid, album=t["album"],
                           tracks=t["tracks"], total=len(t["tracks"]))

@app.route("/dl/<tid>/<file>")
def serve_file(tid, file):
    t = tasks.get(tid)
    if not t or t["status"] != "done":
        abort(404)
    return send_from_directory(os.path.join(t["_dir"], "alac"), file,
                               as_attachment=True, download_name=file)

@app.route("/dl/<tid>/zip")
def serve_zip(tid):
    t = tasks.get(tid)
    if not t or t["status"] != "done":
        abort(404)
    alac_dir = os.path.join(t["_dir"], "alac")
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(os.listdir(alac_dir)):
            zf.write(os.path.join(alac_dir, f), arcname=f)
    buf.seek(0)
    return send_file(buf, mimetype="application/zip", as_attachment=True,
                     download_name=f"{slugify(t['album'])}.zip")

@app.route("/api/send-to-plex/<tid>", methods=["POST"])
def send_to_plex(tid):
    t = tasks.get(tid)
    if not t or t["status"] != "done":
        return jsonify(error="Task not found or not done"), 404
    if not os.path.isdir(PLEX_DIR):
        return jsonify(error="Plex music folder not available"), 200
    first = next((tr for tr in t["tracks"] if tr.get("done")), None)
    if not first:
        return jsonify(error="No completed tracks"), 400
    artist = slugify(first.get("artist", "Unknown Artist"))
    album = slugify(t["album"])
    dest = os.path.join(PLEX_DIR, artist, album)
    os.makedirs(dest, exist_ok=True)
    alac_dir = os.path.join(t["_dir"], "alac")
    count = 0
    for f in sorted(os.listdir(alac_dir)):
        src = os.path.join(alac_dir, f)
        dst = os.path.join(dest, f)
        try:
            if os.path.exists(dst): os.remove(dst)
            shutil.copy2(src, dst)
            count += 1
        except: pass
    return jsonify(status="sent", copied=count, path=f"{artist}/{album}")

@app.route("/api/dl/zip-all")
def serve_all_zips():
    with LOCK:
        done_tasks = [t for t in tasks.values()
                      if t["status"] == "done" and os.path.isdir(os.path.join(t["_dir"], "alac"))]

    if not done_tasks:
        abort(404)

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for t in done_tasks:
            alac_dir = os.path.join(t["_dir"], "alac")
            album = slugify(t["album"])
            for f in sorted(os.listdir(alac_dir)):
                zf.write(os.path.join(alac_dir, f), arcname=f"{album}/{f}")
    buf.seek(0)
    return send_file(buf, mimetype="application/zip", as_attachment=True,
                     download_name="all-downloads.zip")

@app.route("/api/count-done")
def count_done():
    with LOCK:
        count = sum(1 for t in tasks.values() if t["status"] == "done")
    return jsonify(count=count)

@app.route("/api/health")
def health():
    with LOCK:
        active = sum(1 for t in tasks.values() if t["status"] == "downloading")
        done = sum(1 for t in tasks.values() if t["status"] == "done")
        qsize = sum(1 for t in tasks.values() if t["status"] == "info")
        errored = sum(1 for t in tasks.values() if t["status"] == "error")
        total = len(tasks)
    slots = GLOBAL_SLOTS._value
    status_code = 503 if active >= 4 else 200
    return jsonify(
        status="healthy" if status_code == 200 else "busy",
        active_downloads=active,
        completed=done,
        queued=qsize,
        errors=errored,
        total_tasks=total,
        slots_available=slots,
        slots_max=CONCURRENCY,
        rate_limited_ips=len(rate_map),
        uptime_seconds=round(time.monotonic()),
    ), status_code

@app.route("/api/retry-track/<tid>/<int:idx>", methods=["POST"])
def api_retry_track(tid, idx):
    # Gate (2026-09-06): only finished tasks with a FAILED track may retry.
    # Retrying an in-flight task/track used to spawn a second process_track
    # for the same output files and double-consume a GLOBAL_SLOTS slot,
    # wedging the queue. Check + clear happen under LOCK so a double-click
    # cannot slip two workers past the gate.
    with LOCK:
        t = tasks.get(tid)
        if not t or idx >= len(t.get("tracks", [])):
            abort(404)
        if t.get("status") not in ("done", "error"):
            return jsonify(error="Task is still processing"), 409
        track = t["tracks"][idx]
        if track.get("done") or not track.get("error"):
            return jsonify(error="Track is not in a failed state"), 409
        track["done"] = False
        track["error"] = None
        track["path"] = None
    raw_dir = os.path.join(t["_dir"], "raw")
    alac_dir = os.path.join(t["_dir"], "alac")
    os.makedirs(raw_dir, exist_ok=True)
    os.makedirs(alac_dir, exist_ok=True)
    threading.Thread(target=process_track, args=(t, idx, raw_dir, alac_dir, None), daemon=True).start()
    return jsonify(status="retrying")

@app.route("/api/notify", methods=["POST"])
def api_notify():
    url = request.form.get("url", "")
    error = request.form.get("error", "")
    album = request.form.get("album", "")
    msg = f"[ALACarrte] Error: {error}"
    if album:
        msg += f"\nAlbum: {album}"
    if url:
        msg += f"\nURL: {url}"

    results = {}

    if GOTIFY_URL and GOTIFY_TOKEN:
        try:
            payload = json.dumps({"title": "ALACarrte", "message": msg, "priority": 7}).encode()
            req = urllib.request.Request(GOTIFY_URL + "?token=" + GOTIFY_TOKEN,
                                         data=payload,
                                         headers={"Content-Type": "application/json"},
                                         method="POST")
            urllib.request.urlopen(req, timeout=10)
            results["gotify"] = "sent"
        except Exception as e:
            results["gotify"] = str(e)

    if HA_WEBHOOK:
        try:
            ha_payload = json.dumps({"message": msg}).encode()
            req = urllib.request.Request(HA_WEBHOOK, data=ha_payload,
                                         headers={"Content-Type": "application/json"},
                                         method="POST")
            urllib.request.urlopen(req, timeout=10)
            results["ha"] = "sent"
        except Exception as e:
            results["ha"] = str(e)

    return jsonify(results=results)

_RATE_LIMITED = False

def vpn_reconnect():
    """Try to switch VPN server when rate-limited. Runs in background thread."""
    global _RATE_LIMITED
    _RATE_LIMITED = True
    if not VPN_CONTROL:
        return
    try:
        subprocess.run(VPN_CONTROL.split(), timeout=30, capture_output=True)
    except Exception:
        pass
    _RATE_LIMITED = False

@app.route("/api/vpn/switch", methods=["POST"])
def api_vpn_switch():
    if not VPN_CONTROL:
        return jsonify(error="VPN not configured"), 400
    threading.Thread(target=vpn_reconnect, daemon=True).start()
    return jsonify(status="switching")

@app.route("/api/vpn/status")
def api_vpn_status():
    return jsonify(rate_limited=_RATE_LIMITED, proxy=bool(VPN_PROXY), control=bool(VPN_CONTROL))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
