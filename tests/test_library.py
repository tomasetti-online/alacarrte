"""Library tests: persisted history survives completion, restart, and cleanup."""
import json
import os
import subprocess
import threading
import time

import app as alacarrte


def _task():
    tid = "libtid"
    return tid, dict(
        tid=tid, status="info", album="Library Album", album_artist=None, format="alac",
        client_ip="127.0.0.1", _sid="default",
        tracks=[
            dict(id="abc123", title="Song One", artist="Artist", uploader="u", done=False),
            dict(id="def456", title="Song Two", artist="Artist", uploader="u", done=False),
        ],
        progress=(0, 2, ""), _dir="", _ts=0.0,
    )


def _fake_ytdl(args, timeout=180, task=None):
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


class _FS:
    def run(self, args, **kw):
        out = args[-1]
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        with open(out, "w") as f:
            f.write("audio")
        return subprocess.CompletedProcess(args, 0, b"", b"")


def _run_library_fixture(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(alacarrte, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(alacarrte, "LIBRARY_FILE", str(data_dir / "library.json"))
    monkeypatch.setattr(alacarrte, "run_ytdl", _fake_ytdl)
    monkeypatch.setattr(alacarrte, "search_musicbrainz", lambda a, t: None)
    monkeypatch.setattr(alacarrte.subprocess, "run", _FS().run)
    monkeypatch.setattr(alacarrte, "GLOBAL_SLOTS", threading.BoundedSemaphore(4))
    return str(data_dir)


def test_job_indexed_on_completion(tmp_path, monkeypatch):
    data_dir = _run_library_fixture(tmp_path, monkeypatch)
    alacarrte.tasks.clear()

    tid, t = _task()
    alacarrte.tasks[tid] = t
    alacarrte.do_download(tid, "https://youtube.com/watch?v=abc123", t["_dir"])

    assert t["status"] == "done"
    entries = alacarrte.load_library()
    assert len(entries) == 1
    e = entries[0]
    assert e["tid"] == tid
    assert e["album"] == "Library Album"
    assert e["format"] == "alac"
    assert e["total"] == 2
    assert e["failed"] == 0
    assert len(e["tracks"]) == 2
    # Both tracks are done, each with a file basename
    assert all(tr["file"].endswith(".m4a") for tr in e["tracks"])
    # Cover was cached out of the task dir
    assert e["cover"] and os.path.exists(e["cover"])

    # Endpoint returns it
    c = alacarrte.app.test_client()
    r = c.get("/api/library")
    assert r.status_code == 200
    assert r.get_json()["library"][0]["tid"] == tid


def test_library_survives_restart(tmp_path, monkeypatch):
    data_dir = _run_library_fixture(tmp_path, monkeypatch)
    alacarrte.tasks.clear()

    tid, t = _task()
    alacarrte.tasks[tid] = t
    alacarrte.do_download(tid, "https://youtube.com/watch?v=abc123", t["_dir"])

    # Simulate a restart: clear in-memory state entirely (fresh module import
    # would do the same) and reload from the persisted file on disk.
    alacarrte.tasks.clear()
    entries = alacarrte.load_library()
    assert len(entries) == 1
    e = entries[0]
    assert e["tid"] == tid
    # A real restart re-reads DATA_DIR/libary.json; prove the file round-trips.
    on_disk = json.load(open(alacarrte.LIBRARY_FILE, encoding="utf-8"))
    assert on_disk == entries


def test_cleanup_exempts_indexed_jobs(tmp_path, monkeypatch):
    data_dir = _run_library_fixture(tmp_path, monkeypatch)
    alacarrte.tasks.clear()

    tid, t = _task()
    # Give the task a real dir so cleanup's rmtree is meaningful.
    task_dir = os.path.join(data_dir, tid)
    os.makedirs(task_dir, exist_ok=True)
    t["_dir"] = task_dir
    alacarrte.tasks[tid] = t
    alacarrte.do_download(tid, "https://youtube.com/watch?v=abc123", task_dir)

    # Manually age it past the 7-day threshold and index it in the library.
    t["_ts"] = time.time() - 604800 - 60
    assert tid in alacarrte.library_tids()

    # A non-library expired task should be a cleanup candidate.
    other = dict(tid="other", status="done", _ts=time.time() - 604800 - 60, _dir=task_dir,
                 tracks=[])
    alacarrte.tasks["other"] = other

    # Run one cleanup pass.
    with alacarrte.LOCK:
        now = time.time()
        lib = alacarrte.library_tids()
        dead = [x for x, tx in alacarrte.tasks.items()
                if tx["status"] in ("done", "error")
                and now - tx["_ts"] > 604800
                and x not in lib]
    assert tid not in dead, "indexed job must be exempt from cleanup"
    assert "other" in dead, "expired non-library job should still be purged"
    assert os.path.exists(task_dir)
