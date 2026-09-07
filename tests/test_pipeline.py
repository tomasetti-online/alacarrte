"""Pipeline tests for the download core (do_download + process_track).

These cover the part that actually matters and had zero coverage: the
yt-dlp command construction, output post-processing, metadata embedding,
and error handling. Everything external (yt-dlp, ffmpeg, MusicBrainz) is
mocked so we test OUR logic, not third-party behavior.
"""
import os
import subprocess

import pytest

import app as alacarrte


class FakeRun:
    """Stand-in for subprocess.run that records the argv it was handed."""

    def __init__(self):
        self.calls = []
        self.returncode = 0

    def __call__(self, args, **kw):
        self.calls.append(list(args))
        # If this is an ffmpeg invocation, materialize the output file so
        # existence assertions are meaningful.
        if args and args[0] == "ffmpeg":
            out = args[-1]
            os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
            with open(out, "w") as f:
                f.write("audio")
        return subprocess.CompletedProcess(args, self.returncode, b"", b"")


def _make_task(album="Test Album", fmt="alac"):
    tid = "testtid"
    return tid, dict(
        tid=tid, status="info", album=album, album_artist=None, format=fmt,
        client_ip="127.0.0.1", _sid="default",
        tracks=[
            dict(id="abc123", title="First Song - (Official Video)", artist="Test Artist", uploader="u", done=False),
            dict(id="def456", title="Second Song", artist="Test Artist", uploader="u", done=False),
        ],
        progress=(0, 2, ""), _dir="", _ts=0.0,
    )


def _fake_ytdl(out_dir, fake_run):
    def fake(args, timeout=180, task=None):
        # Cover-art call: -o is a bare path (no %(ext)s)
        if "--write-thumbnail" in args:
            o = args[args.index("-o") + 1]
            with open(o + ".jpg", "w") as f:
                f.write("cover")
            return 0, "", ""
        # Main download call: write the flac file the -o template predicts
        o = args[args.index("-o") + 1]
        flac = o.replace("%(ext)s", "flac")
        os.makedirs(os.path.dirname(flac), exist_ok=True)
        with open(flac, "w") as f:
            f.write("flacdata")
        return 0, "", ""
    return fake


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    alacarrte.tasks.clear()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    fake_run = FakeRun()

    monkeypatch.setattr(alacarrte, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(alacarrte, "run_ytdl", _fake_ytdl(data_dir, fake_run))
    monkeypatch.setattr(alacarrte, "search_musicbrainz",
                        lambda artist, title: dict(album="MB Album", year="2020", track=1, total=12))
    monkeypatch.setattr(alacarrte.subprocess, "run", fake_run)
    # Don't let the global semaphore block: run tracks concurrently in the test
    monkeypatch.setattr(alacarrte, "GLOBAL_SLOTS", alacarrte.threading.BoundedSemaphore(4))
    return fake_run


def test_do_download_alac_makes_m4a_with_metadata(pipeline):
    tid, t = _make_task(fmt="alac")
    alacarrte.tasks[tid] = t
    alacarrte.do_download(tid, "https://youtube.com/watch?v=abc123", t["_dir"])

    assert t["status"] == "done"
    assert all(tr.get("done") for tr in t["tracks"])
    for tr in t["tracks"]:
        assert tr["path"].endswith(".m4a")
        assert os.path.exists(tr["path"])

    # A real ffmpeg invocation must have been assembled for each track.
    ff = [c for c in pipeline.calls if c[0] == "ffmpeg"]
    assert len(ff) == 2
    ff0 = ff[0]
    assert "-c:a" in ff0 and ff0[ff0.index("-c:a") + 1] == "alac"
    assert "-c:v" in ff0 and ff0[ff0.index("-c:v") + 1] == "copy"
    # MusicBrainz mock overrode the album + injected track number
    assert any("MB Album" in a for a in ff0)
    assert any("1/12" in a for a in ff0)
    # Cover art attached as embedded picture
    assert any("Album cover" in a for a in ff0)


def test_do_download_flac_copies_instead_of_ffmpeg(pipeline):
    tid, t = _make_task(fmt="flac")
    alacarrte.tasks[tid] = t
    alacarrte.do_download(tid, "https://youtube.com/watch?v=abc123", t["_dir"])

    assert t["status"] == "done"
    ff = [c for c in pipeline.calls if c[0] == "ffmpeg"]
    assert ff == [], "FLAC output must not invoke ffmpeg"
    for tr in t["tracks"]:
        assert tr["path"].endswith(".flac")
        assert os.path.exists(tr["path"])


def test_process_track_ytdl_error_marks_failed(pipeline, monkeypatch):
    tid, t = _make_task(fmt="alac")
    track = t["tracks"][0]

    def always_fail(args, timeout=180, task=None):
        return 1, "", "rate-limited: 429 too many requests"
    monkeypatch.setattr(alacarrte, "run_ytdl", always_fail)

    alacarrte.process_track(t, 0, "/tmp/raw", "/tmp/alac", None)
    assert track["error"] and "Failed after 3 attempts" in track["error"]
    assert not track.get("done")


def test_process_track_no_flac_reports_error(pipeline, monkeypatch, tmp_path):
    tid, t = _make_task(fmt="alac")
    track = t["tracks"][0]

    # yt-dlp returns success but writes no flac file
    def no_output(args, timeout=180, task=None):
        return 0, "", ""
    monkeypatch.setattr(alacarrte, "run_ytdl", no_output)

    raw_dir = tmp_path / "raw"
    alac_dir = tmp_path / "alac"
    raw_dir.mkdir()
    alac_dir.mkdir()
    alacarrte.process_track(t, 0, str(raw_dir), str(alac_dir), None)
    assert track["error"] == "No FLAC produced"
    assert not track.get("done")


def test_clean_title_used_for_output_filename(pipeline, tmp_path):
    tid, t = _make_task(fmt="alac")
    alacarrte.tasks[tid] = t
    alacarrte.do_download(tid, "https://youtube.com/watch?v=abc123", t["_dir"])
    # The "(Official Video)" suffix must be stripped from the filename and metadata
    titles = [tr["title"] for tr in t["tracks"]]
    assert titles[0] == "First Song"
