"""Dedup tests: library matching (recording id + title/artist), Skip gate, Replace."""
import json
import os
import subprocess
import threading
import time

import config
import engine
import library
import state


def _entry(tid, album, tracks):
    return dict(tid=tid, album=album, format="alac", timestamp=0,
                tracks=tracks, total=len(tracks), failed=0)


def _seed_library(entries):
    library.save_library(entries)


def test_match_by_recording_id(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "LIBRARY_FILE", str(tmp_path / "library.json"))
    _seed_library([_entry("t1", "Old Album", [
        dict(title="Song One", artist="Artist", year="2020",
             mb_id="mb-123", file="01 - Song One.m4a"),
    ])])
    dups = library.find_library_duplicates([
        dict(id="x", title="Song One", artist="Artist", mb_id="mb-123"),
    ])
    assert len(dups) == 1
    assert dups[0]["match"] == "recording"
    assert dups[0]["index"] == 0


def test_match_by_title_artist_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "LIBRARY_FILE", str(tmp_path / "library.json"))
    _seed_library([_entry("t1", "Old Album", [
        dict(title="Song One", artist="Artist", file="01 - Song One.m4a"),
    ])])
    dups = library.find_library_duplicates([
        dict(id="x", title="Song One", artist="Artist"),
        dict(id="y", title="Different", artist="Artist"),
    ])
    assert len(dups) == 1
    assert dups[0]["match"] == "title+artist"
    assert dups[0]["index"] == 0


def test_match_by_recording_id_takes_precedence(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "LIBRARY_FILE", str(tmp_path / "library.json"))
    _seed_library([_entry("t1", "Old Album", [
        dict(title="Song One", artist="Artist", mb_id="mb-abc", file="01 - Song One.m4a"),
    ])])
    dups = library.find_library_duplicates([
        dict(id="x", title="Song One", artist="Artist", mb_id="mb-xyz"),
    ])
    # Different recording id: falls through to title+artist, which still matches.
    assert len(dups) == 1
    assert dups[0]["match"] == "title+artist"


def _dump_single_json_song():
    return {
        "_type": "video", "id": "abc123", "title": "Artist - Song One",
        "uploader": "Artist",
    }


def _api_fixture(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(library, "LIBRARY_FILE", str(tmp_path / "library.json"))
    state.tasks.clear()

    def fake_ytdl(args, timeout=180, task=None):
        if "--dump-single-json" in args:
            return 0, json.dumps(_dump_single_json_song()), ""
        if "--write-thumbnail" in args:
            o = args[args.index("-o") + 1]
            os.makedirs(os.path.dirname(o), exist_ok=True)
            with open(o + ".jpg", "w") as f:
                f.write("cover")
            return 0, "", ""
        o = args[args.index("-o") + 1]
        flac = o.replace("%(ext)s", "flac")
        os.makedirs(os.path.dirname(flac), exist_ok=True)
        with open(flac, "w") as f:
            f.write("flacdata")
        return 0, "", ""
    monkeypatch.setattr(engine, "run_ytdl", fake_ytdl)
    monkeypatch.setattr(engine, "search_musicbrainz", lambda a, t: None)

    class _FS:
        def run(self, args, **kw):
            out = args[-1]
            os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
            with open(out, "w") as f:
                f.write("a")
            return subprocess.CompletedProcess(args, 0, b"", b"")
    monkeypatch.setattr(subprocess, "run", _FS().run)
    monkeypatch.setattr(state, "GLOBAL_SLOTS", threading.BoundedSemaphore(4))

    import app
    return app.app.test_client()


def test_skip_does_not_start_new_job(tmp_path, monkeypatch):
    c = _api_fixture(tmp_path, monkeypatch)
    _seed_library([_entry("t1", "Old Album", [
        dict(title="Song One", artist="Artist", file="01 - Song One.m4a"),
    ])])

    resp = c.post("/api/download", data={"url": "https://youtube.com/watch?v=abc123"})
    assert resp.status_code == 409
    body = resp.get_json()
    assert "already in your library" in body["error"]
    assert len(body["duplicates"]) == 1
    assert state.tasks == {}, "Skip must not start a new job"


def test_replace_proceeds_and_updates_library(tmp_path, monkeypatch):
    c = _api_fixture(tmp_path, monkeypatch)
    _seed_library([_entry("t1", "Old Album", [
        dict(title="Song One", artist="Artist", file="01 - Song One.m4a"),
    ])])

    resp = c.post("/api/download", data={"url": "https://youtube.com/watch?v=abc123",
                                         "replace": "1"})
    assert resp.status_code == 200, resp.get_json()
    tid = resp.get_json()["tid"]
    assert tid in state.tasks

    deadline = time.time() + 15
    while time.time() < deadline:
        if state.tasks[tid].get("status") == "done":
            break
        time.sleep(0.2)
    assert state.tasks[tid]["status"] == "done"

    entries = library.load_library()
    assert len(entries) == 1
    assert entries[0]["tid"] == tid
    assert entries[0]["album"] == "Downloaded Music"
    assert all(e["tid"] != "t1" for e in entries)
