"""Summary + Retry Failed tests."""
import os
import subprocess
import threading
import time

import app as alacarrte


def _task(album="Sum Album", fmt="alac"):
    tid = "sumtid"
    return tid, dict(
        tid=tid, status="info", album=album, album_artist=None, format=fmt,
        client_ip="127.0.0.1", _sid="default",
        tracks=[
            dict(id="aa11", title="Song One", artist="Artist", uploader="u", done=False),
            dict(id="bb22", title="Song Two", artist="Artist", uploader="u", done=False),
        ],
        progress=(0, 2, ""), _dir="", _ts=0.0,
    )


class _FS:
    def run(self, args, **kw):
        out = args[-1]
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        with open(out, "w") as f:
            f.write("audio-bytes")
        return subprocess.CompletedProcess(args, 0, b"", b"")


def _fixture(tmp_path, monkeypatch, fail_vid=None, calls=None):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(alacarrte, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(alacarrte, "LIBRARY_FILE", str(data_dir / "library.json"))
    alacarrte.tasks.clear()

    def fake_ytdl(args, timeout=180, task=None):
        if calls is not None:
            calls.append("ytdl")
        if "--write-thumbnail" in args:
            o = args[args.index("-o") + 1]
            os.makedirs(os.path.dirname(o), exist_ok=True)
            with open(o + ".jpg", "w") as f:
                f.write("cover")
            return 0, "", ""
        o = args[args.index("-o") + 1]
        flac = o.replace("%(ext)s", "flac")
        if fail_vid and fail_vid in args[-1]:
            return 1, "", "yt-dlp: unable to download video"
        os.makedirs(os.path.dirname(flac), exist_ok=True)
        with open(flac, "w") as f:
            f.write("flacdata")
        return 0, "", ""
    monkeypatch.setattr(alacarrte, "run_ytdl", fake_ytdl)
    monkeypatch.setattr(alacarrte, "search_musicbrainz", lambda a, t: None)
    monkeypatch.setattr(alacarrte.subprocess, "run", _FS().run)
    monkeypatch.setattr(alacarrte, "GLOBAL_SLOTS", threading.BoundedSemaphore(4))
    return str(data_dir)


def test_summary_reports_counts_and_fields(tmp_path, monkeypatch):
    # One track fails (aa11), the other succeeds -> downloaded=1, failed=1.
    _fixture(tmp_path, monkeypatch, fail_vid="aa11")
    tid, t = _task()
    alacarrte.tasks[tid] = t
    alacarrte.do_download(tid, "https://youtube.com/watch?v=aa11", t["_dir"])

    c = alacarrte.app.test_client()
    r = c.get(f"/api/status/{tid}")
    assert r.status_code == 200
    d = r.get_json()
    assert d["status"] == "done"
    assert d["summary"] is not None
    s = d["summary"]
    assert s["downloaded"] == 1
    assert s["failed"] == 1
    assert s["total"] == 2
    assert s["size_bytes"] > 0, "a completed track must contribute its file size"
    assert s["duration_seconds"] >= 0


def test_summary_zero_failed_clean_batch(tmp_path, monkeypatch):
    _fixture(tmp_path, monkeypatch)
    tid, t = _task()
    alacarrte.tasks[tid] = t
    alacarrte.do_download(tid, "https://youtube.com/watch?v=aa11", t["_dir"])
    c = alacarrte.app.test_client()
    s = c.get(f"/api/status/{tid}").get_json()["summary"]
    assert s["downloaded"] == 2
    assert s["failed"] == 0


def test_retry_failed_reruns_only_failed_tracks(tmp_path, monkeypatch):
    # aa11 fails during the normal batch (status != "retrying"), but succeeds
    # when Retry Failed re-runs it (status == "retrying"). bb22 always works.
    runs = {"aa11": 0, "bb22": 0}

    def fake_ytdl(args, timeout=180, task=None):
        if "--write-thumbnail" in args:
            o = args[args.index("-o") + 1]
            os.makedirs(os.path.dirname(o), exist_ok=True)
            with open(o + ".jpg", "w") as f:
                f.write("cover")
            return 0, "", ""
        o = args[args.index("-o") + 1]
        vid = args[-1].split("watch?v=")[-1]
        if task is not None and task.get("status") != "retrying" and vid == "aa11":
            return 1, "", "yt-dlp: unable to download video"
        runs[vid] = runs.get(vid, 0) + 1
        flac = o.replace("%(ext)s", "flac")
        os.makedirs(os.path.dirname(flac), exist_ok=True)
        with open(flac, "w") as f:
            f.write("flacdata")
        return 0, "", ""
    monkeypatch.setattr(alacarrte, "run_ytdl", fake_ytdl)
    monkeypatch.setattr(alacarrte, "search_musicbrainz", lambda a, t: None)
    monkeypatch.setattr(alacarrte.subprocess, "run", _FS().run)
    monkeypatch.setattr(alacarrte, "GLOBAL_SLOTS", threading.BoundedSemaphore(4))
    data_dir = tmp_path / "data"; data_dir.mkdir()
    monkeypatch.setattr(alacarrte, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(alacarrte, "LIBRARY_FILE", str(data_dir / "library.json"))
    alacarrte.tasks.clear()

    tid, t = _task()
    alacarrte.tasks[tid] = t
    alacarrte.do_download(tid, "https://youtube.com/watch?v=aa11", t["_dir"])
    assert t["tracks"][0]["error"], "first track should have failed initially"
    assert t["tracks"][1]["done"]
    runs_before = dict(runs)

    c = alacarrte.app.test_client()
    r = c.post(f"/api/retry-failed/{tid}")
    assert r.status_code == 200
    assert r.get_json()["retried"] == 1

    # Wait for the worker to finish.
    deadline = time.time() + 15
    while time.time() < deadline and alacarrte.tasks[tid].get("status") != "done":
        time.sleep(0.2)
    assert alacarrte.tasks[tid]["status"] == "done"

    # Only the failed track was re-run (bb22's run count is unchanged).
    assert runs["bb22"] == runs_before["bb22"], "successful track must not be re-run"
    assert runs["aa11"] == runs_before["aa11"] + 1, "failed track must be re-run once"
    assert t["tracks"][0]["done"], "failed track should be done after retry"
    assert not t["tracks"][0].get("error")
    assert t["tracks"][1]["done"], "successful track untouched"


def test_retry_failed_no_failures_rejected(tmp_path, monkeypatch):
    _fixture(tmp_path, monkeypatch)
    tid, t = _task()
    alacarrte.tasks[tid] = t
    alacarrte.do_download(tid, "https://youtube.com/watch?v=aa11", t["_dir"])
    c = alacarrte.app.test_client()
    r = c.post(f"/api/retry-failed/{tid}")
    assert r.status_code == 409
