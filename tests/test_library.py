"""Library tests: persisted history survives completion, restart, and cleanup."""
import json
import os
import subprocess
import threading
import time

import config
import engine
import library
import state
from conftest import FakeSubprocess, fake_ytdl, make_task


def _fixture(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(library, "LIBRARY_FILE", str(data_dir / "library.json"))
    monkeypatch.setattr(engine, "run_ytdl", fake_ytdl())
    monkeypatch.setattr(engine, "search_musicbrainz", lambda a, t: None)
    monkeypatch.setattr(subprocess, "run", FakeSubprocess().run)
    monkeypatch.setattr(state, "GLOBAL_SLOTS", threading.BoundedSemaphore(4))
    state.tasks.clear()
    return str(data_dir)


def test_job_indexed_on_completion(tmp_path, monkeypatch):
    _fixture(tmp_path, monkeypatch)
    t = make_task(album="Library Album", tid="libtid")
    tid = t["tid"]
    state.tasks[tid] = t
    engine.do_download(tid, "https://youtube.com/watch?v=abc123", t["_dir"])

    assert t["status"] == "done"
    entries = library.load_library()
    assert len(entries) == 1
    e = entries[0]
    assert e["tid"] == tid
    assert e["album"] == "Library Album"
    assert e["format"] == "alac"
    assert e["total"] == 2
    assert e["failed"] == 0
    assert len(e["tracks"]) == 2
    assert all(tr["file"].endswith(".m4a") for tr in e["tracks"])
    assert e["cover"] and os.path.exists(e["cover"])

    c = app_client()
    r = c.get("/api/library")
    assert r.status_code == 200
    assert r.get_json()["library"][0]["tid"] == tid


def app_client():
    import app
    return app.app.test_client()


def test_library_survives_restart(tmp_path, monkeypatch):
    _fixture(tmp_path, monkeypatch)
    t = make_task(album="Library Album", tid="libtid")
    tid = t["tid"]
    state.tasks[tid] = t
    engine.do_download(tid, "https://youtube.com/watch?v=abc123", t["_dir"])

    # Simulate a restart: clear in-memory state and reload from the persisted file.
    state.tasks.clear()
    entries = library.load_library()
    assert len(entries) == 1
    assert entries[0]["tid"] == tid
    on_disk = json.load(open(library.LIBRARY_FILE, encoding="utf-8"))
    assert on_disk == entries


def test_cleanup_exempts_indexed_jobs(tmp_path, monkeypatch):
    data_dir = _fixture(tmp_path, monkeypatch)
    t = make_task(album="Library Album", tid="libtid")
    tid = t["tid"]
    task_dir = os.path.join(data_dir, tid)
    os.makedirs(task_dir, exist_ok=True)
    t["_dir"] = task_dir
    state.tasks[tid] = t
    engine.do_download(tid, "https://youtube.com/watch?v=abc123", task_dir)

    # Age it past the 7-day threshold; it stays indexed so cleanup spares it.
    t["_ts"] = time.time() - 604800 - 60
    assert tid in library.library_tids()

    other = dict(tid="other", status="done", _ts=time.time() - 604800 - 60, _dir=task_dir, tracks=[])
    state.tasks["other"] = other

    with state.LOCK:
        now = time.time()
        lib = library.library_tids()
        dead = [x for x, tx in state.tasks.items()
                if tx["status"] in ("done", "error")
                and now - tx["_ts"] > 604800
                and x not in lib]
    assert tid not in dead, "indexed job must be exempt from cleanup"
    assert "other" in dead, "expired non-library job should still be purged"
    assert os.path.exists(task_dir)
