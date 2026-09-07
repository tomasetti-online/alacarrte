"""Pipeline tests for the download core (engine.do_download + process_track).

These cover the part that actually matters: the yt-dlp command construction,
output post-processing, metadata embedding, and error handling. Everything
external (yt-dlp, ffmpeg, MusicBrainz) is mocked so we test OUR logic.
"""
import os
import subprocess
import threading

import pytest

import engine
import state
from conftest import FakeSubprocess, fake_ytdl, make_task


@pytest.fixture
def pipeline(app_env, monkeypatch):
    """Point engine at fresh dirs and mock its external deps."""
    fake_run = FakeSubprocess()
    monkeypatch.setattr(engine, "run_ytdl", fake_ytdl())
    monkeypatch.setattr(engine, "search_musicbrainz",
                        lambda artist, title: dict(album="MB Album", year="2020", track=1, total=12))
    monkeypatch.setattr(subprocess, "run", fake_run.run)
    return fake_run


def _start_task(fmt="alac"):
    tid = "testtid"
    t = make_task(fmt=fmt)
    state.tasks[tid] = t
    return tid, t


def test_do_download_alac_makes_m4a_with_metadata(pipeline):
    tid, t = _start_task(fmt="alac")
    engine.do_download(tid, "https://youtube.com/watch?v=abc123", t["_dir"])

    assert t["status"] == "done"
    assert all(tr.get("done") for tr in t["tracks"])
    for tr in t["tracks"]:
        assert tr["path"].endswith(".m4a")
        assert os.path.exists(tr["path"])

    ff = [c for c in pipeline.calls if c[0] == "ffmpeg"]
    assert len(ff) == 2
    ff0 = ff[0]
    assert "-c:a" in ff0 and ff0[ff0.index("-c:a") + 1] == "alac"
    assert "-c:v" in ff0 and ff0[ff0.index("-c:v") + 1] == "copy"
    assert any("MB Album" in a for a in ff0)
    assert any("1/12" in a for a in ff0)
    assert any("Album cover" in a for a in ff0)


def test_do_download_flac_copies_instead_of_ffmpeg(pipeline):
    tid, t = _start_task(fmt="flac")
    engine.do_download(tid, "https://youtube.com/watch?v=abc123", t["_dir"])

    assert t["status"] == "done"
    ff = [c for c in pipeline.calls if c[0] == "ffmpeg"]
    assert ff == [], "FLAC output must not invoke ffmpeg"
    for tr in t["tracks"]:
        assert tr["path"].endswith(".flac")
        assert os.path.exists(tr["path"])


def test_process_track_ytdl_error_marks_failed(pipeline, monkeypatch):
    t = make_task(fmt="alac")
    track = t["tracks"][0]

    def always_fail(args, timeout=180, task=None):
        return 1, "", "rate-limited: 429 too many requests"
    monkeypatch.setattr(engine, "run_ytdl", always_fail)

    engine.process_track(t, 0, "/tmp/raw", "/tmp/alac", None)
    assert track["error"] and "Failed after 3 attempts" in track["error"]
    assert not track.get("done")


def test_process_track_no_flac_reports_error(pipeline, monkeypatch, tmp_path):
    t = make_task(fmt="alac")
    track = t["tracks"][0]

    def no_output(args, timeout=180, task=None):
        return 0, "", ""
    monkeypatch.setattr(engine, "run_ytdl", no_output)

    raw_dir = tmp_path / "raw"
    alac_dir = tmp_path / "alac"
    raw_dir.mkdir()
    alac_dir.mkdir()
    engine.process_track(t, 0, str(raw_dir), str(alac_dir), None)
    assert track["error"] == "No FLAC produced"
    assert not track.get("done")


def test_clean_title_used_for_output_filename(pipeline):
    tid, t = _start_task(fmt="alac")
    engine.do_download(tid, "https://youtube.com/watch?v=abc123", t["_dir"])
    titles = [tr["title"] for tr in t["tracks"]]
    assert titles[0] == "First Song"


def test_batch_completes_when_a_track_errors(pipeline, monkeypatch):
    """A batch with an errored track must terminate, not hang forever."""
    tid, t = _start_task(fmt="alac")
    monkeypatch.setattr(engine, "run_ytdl", fake_ytdl(fail_vid="abc123"))

    result = {}
    def run():
        result["done"] = False
        engine.do_download(tid, "https://youtube.com/watch?v=abc123", t["_dir"])
        result["done"] = True
    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(timeout=30)

    assert result.get("done"), "do_download must return even when a track errors"
    assert t["status"] == "done"

    by_id = {tr["id"]: tr for tr in t["tracks"]}
    assert "error" in by_id["abc123"], "the failed track must carry an error"
    assert not by_id["abc123"].get("done"), "failed track must not be marked done"
    assert by_id["def456"].get("done"), "the healthy track must still complete"