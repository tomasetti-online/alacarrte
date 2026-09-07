"""ALACarrte download library: persistence, dedup matching, batch summary.

Owns the persisted history (library.json), the single source of truth for the
"does this track match something already downloaded?" policy, and the
end-of-batch summary computation. Engine and routes call into here; nothing
here knows about Flask or the task threadpool.
"""
import json
import os
import shutil
import time

import config

LIBRARY_FILE = os.path.join(config.DATA_DIR, "library.json")


def load_library():
    if not os.path.exists(LIBRARY_FILE):
        return []
    try:
        with open(LIBRARY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_library(entries):
    with open(LIBRARY_FILE, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)


def library_tids():
    return {e.get("tid") for e in load_library()}


def _norm_key(s):
    return (s or "").strip().lower()


def _matches(tr, other):
    """True if two track dicts refer to the same recording.

    Prefers a MusicBrainz recording id when both sides carry it; otherwise
    falls back to a normalized (artist, title) comparison.
    """
    if tr.get("mb_id") and other.get("mb_id"):
        return tr["mb_id"] == other["mb_id"]
    return (_norm_key(tr.get("artist")), _norm_key(tr.get("title"))) == \
           (_norm_key(other.get("artist")), _norm_key(other.get("title")))


def find_library_duplicates(tracks):
    """Return [{index, title, artist, match}] for tracks already in the library.

    Recording-id match takes precedence; fallback is (artist, title).
    """
    lib = load_library()
    by_id = set()
    by_ta = set()
    for e in lib:
        for tr in e.get("tracks", []):
            if tr.get("mb_id"):
                by_id.add(tr["mb_id"])
            key = (_norm_key(tr.get("artist")), _norm_key(tr.get("title")))
            if key[1]:
                by_ta.add(key)

    dups = []
    for i, tr in enumerate(tracks):
        mb = tr.get("mb_id")
        if mb and mb in by_id:
            dups.append(dict(index=i, title=tr.get("title"),
                             artist=tr.get("artist"), match="recording"))
            continue
        key = (_norm_key(tr.get("artist")), _norm_key(tr.get("title")))
        if key in by_ta:
            dups.append(dict(index=i, title=tr.get("title"),
                             artist=tr.get("artist"), match="title+artist"))
    return dups


def _record_library(t):
    """Write (or update) the persisted library entry for a finished task.

    Used by do_download on completion and by Retry Failed after re-running.
    Reuses any already-copied cover so a re-run doesn't orphan the artwork.
    """
    tid = t["tid"]
    cover_path = t.get("_cover")
    try:
        lib = load_library()
        lib = [e for e in lib if e.get("tid") != tid]
        if t.get("_replace"):
            # A Replace proceeded: drop prior library entries whose tracks
            # collide with this job, so the library doesn't accumulate dupes.
            lib = [e for e in lib
                   if not any(_matches(tr, done_tr)
                              for tr in e.get("tracks", [])
                              for done_tr in t["tracks"]
                              if done_tr.get("done"))]
        entry = dict(
            tid=tid, album=t["album"], format=t.get("format", "alac"),
            timestamp=t.get("_ts", time.time()),
            tracks=[dict(title=tr.get("title"), artist=tr.get("artist"),
                         year=tr.get("year", ""), mb_id=tr.get("mb_id"),
                         file=os.path.basename(tr["path"]) if tr.get("path") else None)
                    for tr in t["tracks"] if tr.get("done")],
            total=len(t["tracks"]),
            failed=sum(1 for tr in t["tracks"] if tr.get("error")),
        )
        if cover_path and os.path.exists(cover_path):
            lib_cover_dir = os.path.join(config.DATA_DIR, "_library_covers")
            os.makedirs(lib_cover_dir, exist_ok=True)
            lib_cover = os.path.join(lib_cover_dir, tid + ".jpg")
            shutil.copy2(cover_path, lib_cover)
            entry["cover"] = lib_cover
        lib.insert(0, entry)
        save_library(lib[:config.LIBRARY_CAP])
    except Exception:
        pass


def _batch_summary(t):
    """Compute the end-of-batch summary for a finished task."""
    downloaded = sum(1 for tr in t.get("tracks", []) if tr.get("done"))
    failed = sum(1 for tr in t.get("tracks", []) if tr.get("error"))
    size = 0
    for tr in t.get("tracks", []):
        if tr.get("done") and tr.get("path") and os.path.exists(tr["path"]):
            size += os.path.getsize(tr["path"])
    start = t.get("_start") or t.get("_ts") or time.time()
    duration = max(0, int(t.get("_ts", time.time()) - start))
    return dict(downloaded=downloaded, failed=failed, total=len(t.get("tracks", [])),
                size_bytes=size, duration_seconds=duration)