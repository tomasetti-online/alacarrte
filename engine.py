"""ALACarrte download engine.

The core pipeline: yt-dlp invocations, MusicBrainz enrichment, the per-batch
download coordinator (do_download), per-track processing (process_track), the
retry workers, background cleanup, VPN reconnection, and the text helpers the
pipeline needs. Everything here is about getting files on disk — routes in
app.py call in, and state/library are the owners of shared state and history.

run_ytdl reads the caller's session cookie id for cookies; when called from a
worker thread there is no request context, which it handles by falling back to
the default session. This is the one place the engine touches Flask.
"""
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
import urllib.request

from flask import session

import config
import library
import state


def slugify(s):
    s = re.sub(r'[\\/:*?"<>|]', '', s).strip()
    return s[:120]


def clean_title(title):
    title = re.sub(r'(?i)\s*\(Official\s+Music\s+Video\)', '', title)
    title = re.sub(r'(?i)\s*\(Official\s+Video\)', '', title)
    title = re.sub(r'(?i)\s*\(Official\s+Audio\)', '', title)
    title = re.sub(r'(?i)\s*\(Music\s+Video\)', '', title)
    title = re.sub(r'(?i)\s*\(Official\s+Lyric[s]?\s+Video\)', '', title)
    title = re.sub(r'(?i)\s*\(Lyric[s]?\s+Video\)', '', title)
    title = re.sub(r'(?i)\s*\[Official\s+Music\s+Video\]', '', title)
    title = re.sub(r'(?i)\s*\[Official\s+Video\]', '', title)
    title = re.sub(r'(?i)\s*\[Official\s+Audio\]', '', title)
    title = re.sub(r'(?i)\s*\[Video\]', '', title)
    title = re.sub(r'(?i)\s*\[Audio\]', '', title)
    title = re.sub(r'(?i)\s*with\s+(lyrics|download)(\s+(lyrics|download))?\s+link.*$', '', title)
    title = re.sub(r'(?i)\s*Official\s+Lyrics?\s*(Video)?$', '', title)
    title = re.sub(r'\s+', ' ', title).strip()
    title = re.sub(r'[\s\-\.\?,;:]+$', '', title).strip()
    return title


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
    cookie_dir = os.path.join(config.DATA_DIR, "cookies")
    sc = os.path.join(cookie_dir, f"{sid}.txt")
    gc = os.path.join(config.DATA_DIR, "cookies.txt")
    if os.path.exists(sc):
        cmd = cmd[:1] + ["--cookies", sc] + cmd[1:]
    elif os.path.exists(gc):
        cmd = cmd[:1] + ["--cookies", gc] + cmd[1:]
    if config.VPN_PROXY and state._RATE_LIMITED:
        cmd = cmd[:1] + ["--proxy", config.VPN_PROXY] + cmd[1:]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    # Check for rate limiting in output and trigger VPN switch
    if config.VPN_PROXY and (r.returncode != 0) and ("rate-limited" in r.stderr.lower() or "429" in r.stderr or "too many requests" in r.stderr.lower()):
        threading.Thread(target=vpn_reconnect, daemon=True).start()
    return r.returncode, r.stdout, r.stderr


def search_musicbrainz(artist, title):
    """Look up track metadata from MusicBrainz. Returns dict with album, year, track, total, or None."""
    now = time.time()
    if now - state._MB_LAST < 1.2:
        time.sleep(1.2 - (now - state._MB_LAST))
    query = urllib.parse.quote(f'artist:"{artist}" AND recording:"{title}"')
    url = f"https://musicbrainz.org/ws/2/recording/?query={query}&fmt=json&limit=5"
    req = urllib.request.Request(url, headers={"User-Agent": "ALACarrte/1.0 ( github.com/tomasetti-online/alacarrte )"})
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
    except Exception:
        return None
    finally:
        state._MB_LAST = time.time()

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
                for media in release.get("media", []):
                    for track in media.get("tracks", []):
                        rt = track.get("recording", {})
                        if rt.get("id") == rec.get("id"):
                            track_num = track.get("position")
                            total_tracks = media.get("track-count") or media.get("track_count")
                            break

            return dict(album=album, year=year,
                        track=track_num, total=total_tracks,
                        source="MusicBrainz", id=rec.get("id"))
    return None


def bg_cleanup():
    while True:
        time.sleep(config.CLEANUP_INTERVAL)
        now = time.time()
        with state.LOCK:
            lib = library.library_tids()
            dead = [tid for tid, t in state.tasks.items()
                    if t["status"] in ("done", "error")
                    and now - t["_ts"] > 604800  # 7 days
                    and tid not in lib]  # library jobs are exempt
            for tid in dead:
                shutil.rmtree(state.tasks[tid]["_dir"], ignore_errors=True)
                del state.tasks[tid]


def do_download(tid, url, folder):
    t = state.tasks[tid]
    t["status"] = "downloading"
    t["_start"] = time.time()
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
    t["_cover"] = cover_path

    done_count = [0]
    dl_lock = threading.Lock()
    finished = [False] * total

    def run_one(idx):
        try:
            process_track(t, idx, raw_dir, alac_dir, cover_path)
        finally:
            with dl_lock:
                done_count[0] += 1
                finished[idx] = True
                t["progress"] = (done_count[0], total, t["tracks"][idx].get("title", ""))

    for idx in range(total):
        if not t["tracks"][idx].get("done"):
            th = threading.Thread(target=run_one, args=(idx,), daemon=True)
            th.start()

    # Keep waiting until every track reaches a terminal state: done OR error.
    # (An errored track never sets done, but it must still advance batch
    # completion or a single dead track would hang the whole batch forever.)
    last_progress = time.time()
    last_done = 0
    while True:
        with dl_lock:
            terminal = [t["tracks"][i].get("done") or t["tracks"][i].get("error")
                        for i in range(total)]
            all_terminal = all(terminal)
            current_terminal = sum(1 for x in terminal if x)
        if all_terminal:
            break
        # If no progress for 10 minutes, fail remaining tracks
        if current_terminal > 0 and current_terminal == last_done:
            if time.time() - last_progress > 600:
                break
        else:
            last_progress = time.time()
            last_done = current_terminal
        time.sleep(5)
    # Mark any stuck tracks as failed
    for i in range(total):
        if not t["tracks"][i].get("done", False) and not t["tracks"][i].get("error"):
            t["tracks"][i]["error"] = "Timed out waiting for slot"

    t["progress"] = (total, total, "Done")
    t["status"] = "done"
    t["_ts"] = time.time()

    # Record the completed job in the persisted library (also used by Retry
    # Failed after re-running, so the entry stays in sync).
    library._record_library(t)

    # Auto-send to Plex if available (only for admin IPs)
    try:
        if config.PLEX_DIR and os.path.isdir(config.PLEX_DIR) and t.get("client_ip") in config.ADMIN_IPS:
            done_tracks = [tr for tr in t["tracks"] if tr.get("done")]
            if done_tracks:
                artists = set(tr.get("artist", "Unknown Artist") for tr in done_tracks)
                album = slugify(t["album"])
                for tr in done_tracks:
                    artist = slugify(tr.get("artist", "Unknown Artist"))
                    sub = "Various Artists" if len(artists) > 1 else artist
                    dest = os.path.join(config.PLEX_DIR, sub, album)
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

    state.GLOBAL_SLOTS.acquire()
    try:
        last_err = ""
        for attempt in range(3):
            code, _, err = run_ytdl([
                "-x", "--audio-format", "flac",
                "--sleep-requests", config.SLEEP_REQUESTS,
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
            if mb.get("id"): track["mb_id"] = mb["id"]

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
        state.GLOBAL_SLOTS.release()


def retry_track(tid, idx):
    """Reset a single failed track and re-run it. Caller has gated + cleared it."""
    t = state.tasks[tid]
    raw_dir = os.path.join(t["_dir"], "raw")
    alac_dir = os.path.join(t["_dir"], "alac")
    os.makedirs(raw_dir, exist_ok=True)
    os.makedirs(alac_dir, exist_ok=True)
    threading.Thread(target=process_track, args=(t, idx, raw_dir, alac_dir, None), daemon=True).start()


def _retry_failed_worker(t):
    """Re-run every failed track of a finished batch, then finalize it.

    Runs after the caller has reset the failed tracks under LOCK. Each track
    is re-processed via process_track (which rebuilds its URL from track id),
    successful tracks are left untouched, and once all reach a terminal state
    the batch is marked done and its library entry is refreshed.
    """
    raw_dir = os.path.join(t["_dir"], "raw")
    alac_dir = os.path.join(t["_dir"], "alac")
    os.makedirs(raw_dir, exist_ok=True)
    os.makedirs(alac_dir, exist_ok=True)
    cover = t.get("_cover")

    indices = [i for i, tr in enumerate(t["tracks"]) if not tr.get("done")]
    threads = []
    for i in indices:
        th = threading.Thread(target=process_track,
                              args=(t, i, raw_dir, alac_dir, cover), daemon=True)
        th.start()
        threads.append(th)
    for th in threads:
        th.join()

    # Mark any that still aren't done as failed again so the batch terminates.
    for i in indices:
        if not t["tracks"][i].get("done") and not t["tracks"][i].get("error"):
            t["tracks"][i]["error"] = "Retry failed to produce output"

    t["progress"] = (len(t["tracks"]), len(t["tracks"]), "Done")
    t["status"] = "done"
    t["_ts"] = time.time()
    library._record_library(t)


def vpn_reconnect():
    """Try to switch VPN server when rate-limited. Runs in background thread."""
    state._RATE_LIMITED = True
    if not config.VPN_CONTROL:
        return
    try:
        subprocess.run(config.VPN_CONTROL.split(), timeout=30, capture_output=True)
    except Exception:
        pass
    state._RATE_LIMITED = False