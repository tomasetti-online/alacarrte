"""ALACarrte web interface.

Thin Flask layer: request handling, session, rate limiting, and every route.
All download logic lives in engine, all history/dedup policy in library, and
shared mutable state in state. Routes only marshal HTTP <-> those modules.
"""
import json
import os
import re
import shutil
import threading
import time
import urllib.request
import uuid
import zipfile
from io import BytesIO

from flask import (Flask, render_template, request, jsonify, send_file,
                   send_from_directory, abort, session)

import config
import engine
import library
import state

app = Flask(__name__)
app.secret_key = config.SECRET_KEY

# Start the background task purger once at process start.
threading.Thread(target=engine.bg_cleanup, daemon=True).start()


def _parse_songs(entries):
    """Turn raw yt-dlp entries into our track dicts."""
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
        songs.append(dict(id=e.get("id"), title=parsed_title,
                          artist=parsed_artist, uploader=uploader))
    return songs


@app.before_request
def ensure_session():
    if not session.get("sid"):
        session["sid"] = uuid.uuid4().hex[:12]


def check_rate(ip, action="request", max_per_min=config.RATE_LIMIT, cooldown=0):
    """Rate limit per IP. Returns (allowed: bool, wait_seconds: int)."""
    if ip.startswith(("10.", "192.168.", "172.16.", "127.", "::1")) or ip in config.ADMIN_IPS:
        return True, 0
    now = time.monotonic()
    with state.LOCK:
        entries = state.rate_map.get(ip, [])
        entries = [t for t in entries if now - t < 60]
        if len(entries) >= max_per_min:
            return False, max(1, int(60 - (now - entries[0])))
        if cooldown and entries and now - entries[-1] < cooldown:
            return False, max(1, int(cooldown - (now - entries[-1])))
        entries.append(now)
        state.rate_map[ip] = entries
    return True, 0


@app.before_request
def rate_limit_middleware():
    ip = request.remote_addr or "unknown"
    if request.path.startswith("/api/download"):
        allowed, wait = check_rate(ip, cooldown=config.DL_COOLDOWN)
        if not allowed:
            return jsonify(error=f"Too many downloads. Wait {wait}s."), 429
    elif request.path.startswith("/api/") and request.method != "GET":
        allowed, wait = check_rate(ip)
        if not allowed:
            return jsonify(error=f"Rate limited. Wait {wait}s."), 429


@app.route("/settings")
def settings_page():
    return render_template("settings.html")


@app.route("/api/cookies", methods=["GET", "POST", "DELETE"])
def api_cookies():
    sid = session.get("sid", "default")
    cookie_dir = os.path.join(config.DATA_DIR, "cookies")
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


@app.route("/")
def index():
    return render_template("index.html", ad_script=config.AD_SCRIPT)


@app.route("/api/info", methods=["POST"])
def api_info():
    url = request.form.get("url", "").strip()
    if not url:
        return jsonify(error="Enter a URL"), 400
    code, out, err = engine.run_ytdl(["--dump-single-json", "--flat-playlist", url])
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

    songs = _parse_songs(entries)
    return jsonify(playlist=playlist_title, tracks=songs,
                   duplicates=library.find_library_duplicates(songs))


@app.route("/api/channel", methods=["POST"])
def api_channel():
    url = request.form.get("url", "").strip()
    if not url:
        return jsonify(error="Enter a channel URL"), 400

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

    if channel_handle and not channel_id:
        handle_clean = channel_handle.replace("-", " ")
        code, out, _ = engine.run_ytdl(["--flat-playlist", "--dump-single-json", "--playlist-end", "1",
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

    releases_url = f"https://www.youtube.com/channel/{channel_id}/releases"
    code, out, err = engine.run_ytdl(["--flat-playlist", "--dump-single-json", releases_url])
    if code != 0:
        return jsonify(error=err.strip() or "Failed to fetch releases"), 400
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return jsonify(error="Could not parse releases"), 500

    channel_title = data.get("title", "")
    releases = []
    for e in data.get("entries", []):
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


@app.route("/api/download", methods=["POST"])
def api_download():
    url = request.form.get("url", "").strip()
    if not url:
        return jsonify(error="Enter a URL"), 400
    album_override = request.form.get("album", "").strip()
    artist_override = request.form.get("artist", "").strip()
    output_format = request.form.get("format", "alac").strip()
    replace = request.form.get("replace", "") == "1"

    code, out, err = engine.run_ytdl(["--dump-single-json", "--flat-playlist", url])
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
    folder = os.path.join(config.DATA_DIR, tid)
    os.makedirs(folder, exist_ok=True)

    album = album_override or playlist_title or "Downloaded Music"
    songs = _parse_songs(entries)
    for s in songs:
        s["done"] = False

    # Pre-flight dedup gate: if these tracks are already in the library and
    # the user didn't ask to Replace, refuse before starting a new job.
    dups = library.find_library_duplicates(songs)
    if dups and not replace:
        return jsonify(error="Track(s) already in your library",
                       duplicates=dups), 409

    t = dict(
        tid=tid, status="info", album=album,
        album_artist=artist_override or None,
        format=output_format,
        client_ip=request.remote_addr,
        _sid=session.get("sid", "default"),
        tracks=songs, progress=(0, len(songs), ""),
        _dir=folder, _ts=time.time(), _replace=replace,
    )
    state.tasks[tid] = t
    threading.Thread(target=engine.do_download, args=(tid, url, folder), daemon=True).start()

    return jsonify(tid=tid, redirect=f"/status/{tid}")


@app.route("/api/status/<tid>")
def api_status(tid):
    t = state.tasks.get(tid)
    if not t:
        return jsonify(error="Task not found"), 404
    return jsonify(
        status=t["status"], album=t["album"], format=t.get("format", "alac"),
        tracks=[dict(title=tr["title"], artist=tr["artist"],
                     done=tr.get("done", False), error=tr.get("error"),
                     file=os.path.basename(tr["path"]) if tr.get("path") else None)
                for tr in t["tracks"]],
        progress=list(t["progress"]),
        summary=library._batch_summary(t) if t.get("status") in ("done", "error") else None,
    )


@app.route("/api/library")
def api_library():
    """Return the persisted download library, newest first."""
    return jsonify(library=library.load_library())


@app.route("/dl/<tid>/cover")
def serve_cover(tid):
    """Serve the cached library cover for a completed job, if present."""
    for e in library.load_library():
        if e.get("tid") == tid and e.get("cover") and os.path.exists(e["cover"]):
            return send_file(e["cover"], mimetype="image/jpeg")
    abort(404)


@app.route("/status/<tid>")
def status_page(tid):
    t = state.tasks.get(tid)
    if not t:
        return render_template("index.html", error="Task not found")
    return render_template("status.html", tid=tid, album=t["album"],
                           tracks=t["tracks"], total=len(t["tracks"]))


@app.route("/dl/<tid>/<file>")
def serve_file(tid, file):
    t = state.tasks.get(tid)
    if not t or t["status"] != "done":
        abort(404)
    return send_from_directory(os.path.join(t["_dir"], "alac"), file,
                               as_attachment=True, download_name=file)


@app.route("/dl/<tid>/zip")
def serve_zip(tid):
    t = state.tasks.get(tid)
    if not t or t["status"] != "done":
        abort(404)
    alac_dir = os.path.join(t["_dir"], "alac")
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(os.listdir(alac_dir)):
            zf.write(os.path.join(alac_dir, f), arcname=f)
    buf.seek(0)
    return send_file(buf, mimetype="application/zip", as_attachment=True,
                     download_name=f"{engine.slugify(t['album'])}.zip")


@app.route("/api/send-to-plex/<tid>", methods=["POST"])
def send_to_plex(tid):
    t = state.tasks.get(tid)
    if not t or t["status"] != "done":
        return jsonify(error="Task not found or not done"), 404
    if not os.path.isdir(config.PLEX_DIR):
        return jsonify(error="Plex music folder not available"), 200
    first = next((tr for tr in t["tracks"] if tr.get("done")), None)
    if not first:
        return jsonify(error="No completed tracks"), 400
    artist = engine.slugify(first.get("artist", "Unknown Artist"))
    album = engine.slugify(t["album"])
    dest = os.path.join(config.PLEX_DIR, artist, album)
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
    with state.LOCK:
        done_tasks = [t for t in state.tasks.values()
                      if t["status"] == "done" and os.path.isdir(os.path.join(t["_dir"], "alac"))]

    if not done_tasks:
        abort(404)

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for t in done_tasks:
            alac_dir = os.path.join(t["_dir"], "alac")
            album = engine.slugify(t["album"])
            for f in sorted(os.listdir(alac_dir)):
                zf.write(os.path.join(alac_dir, f), arcname=f"{album}/{f}")
    buf.seek(0)
    return send_file(buf, mimetype="application/zip", as_attachment=True,
                     download_name="all-downloads.zip")


@app.route("/api/count-done")
def count_done():
    with state.LOCK:
        count = sum(1 for t in state.tasks.values() if t["status"] == "done")
    return jsonify(count=count)


@app.route("/api/health")
def health():
    with state.LOCK:
        active = sum(1 for t in state.tasks.values() if t["status"] == "downloading")
        done = sum(1 for t in state.tasks.values() if t["status"] == "done")
        qsize = sum(1 for t in state.tasks.values() if t["status"] == "info")
        errored = sum(1 for t in state.tasks.values() if t["status"] == "error")
        total = len(state.tasks)
    slots = state.GLOBAL_SLOTS._value
    status_code = 503 if active >= 4 else 200
    return jsonify(
        status="healthy" if status_code == 200 else "busy",
        active_downloads=active,
        completed=done,
        queued=qsize,
        errors=errored,
        total_tasks=total,
        slots_available=slots,
        slots_max=config.CONCURRENCY,
        rate_limited_ips=len(state.rate_map),
        uptime_seconds=round(time.monotonic()),
    ), status_code


@app.route("/api/retry-track/<tid>/<int:idx>", methods=["POST"])
def api_retry_track(tid, idx):
    # Gate (2026-09-06): only finished tasks with a FAILED track may retry.
    # Retrying an in-flight task/track used to spawn a second process_track
    # for the same output files and double-consume a GLOBAL_SLOTS slot,
    # wedging the queue. Check + clear happen under LOCK so a double-click
    # cannot slip two workers past the gate.
    with state.LOCK:
        t = state.tasks.get(tid)
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
    engine.retry_track(tid, idx)
    return jsonify(status="retrying")


@app.route("/api/retry-failed/<tid>", methods=["POST"])
def api_retry_failed(tid):
    # One-click Retry Failed: re-run all failed tracks of a finished batch.
    # Same gating as per-track retry so an in-flight task can't be retried.
    with state.LOCK:
        t = state.tasks.get(tid)
        if not t:
            abort(404)
        if t.get("status") not in ("done", "error"):
            return jsonify(error="Task is still processing"), 409
        failed = [i for i, tr in enumerate(t["tracks"])
                  if not tr.get("done") and tr.get("error")]
        if not failed:
            return jsonify(error="No failed tracks to retry"), 409
        for i in failed:
            t["tracks"][i]["done"] = False
            t["tracks"][i]["error"] = None
            t["tracks"][i]["path"] = None
        t["status"] = "retrying"
    threading.Thread(target=engine._retry_failed_worker, args=(t,), daemon=True).start()
    return jsonify(status="retrying", retried=len(failed))


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

    if config.GOTIFY_URL and config.GOTIFY_TOKEN:
        try:
            payload = json.dumps({"title": "ALACarrte", "message": msg, "priority": 7}).encode()
            req = urllib.request.Request(config.GOTIFY_URL + "?token=" + config.GOTIFY_TOKEN,
                                         data=payload,
                                         headers={"Content-Type": "application/json"},
                                         method="POST")
            urllib.request.urlopen(req, timeout=10)
            results["gotify"] = "sent"
        except Exception as e:
            results["gotify"] = str(e)

    if config.HA_WEBHOOK:
        try:
            ha_payload = json.dumps({"message": msg}).encode()
            req = urllib.request.Request(config.HA_WEBHOOK, data=ha_payload,
                                         headers={"Content-Type": "application/json"},
                                         method="POST")
            urllib.request.urlopen(req, timeout=10)
            results["ha"] = "sent"
        except Exception as e:
            results["ha"] = str(e)

    return jsonify(results=results)


@app.route("/api/vpn/switch", methods=["POST"])
def api_vpn_switch():
    if not config.VPN_CONTROL:
        return jsonify(error="VPN not configured"), 400
    threading.Thread(target=engine.vpn_reconnect, daemon=True).start()
    return jsonify(status="switching")


@app.route("/api/vpn/status")
def api_vpn_status():
    return jsonify(rate_limited=state._RATE_LIMITED, proxy=bool(config.VPN_PROXY),
                   control=bool(config.VPN_CONTROL))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)