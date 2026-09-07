"""Shared test fixtures for ALACarrte.

Centralizes the mock yt-dlp, fake subprocess, and task builders that the
pipeline/library/dedup/summary tests all relied on, and wires each test to a
fresh DATA_DIR so the persisted library can't leak between tests.
"""
import json
import os
import subprocess
import threading

import pytest

import config
import engine
import library
import state


def make_task(album="Test Album", fmt="alac", tid="testtid"):
    return dict(
        tid=tid, status="info", album=album, album_artist=None, format=fmt,
        client_ip="127.0.0.1", _sid="default",
        tracks=[
            dict(id="abc123", title="First Song - (Official Video)", artist="Test Artist", uploader="u", done=False),
            dict(id="def456", title="Second Song", artist="Test Artist", uploader="u", done=False),
        ],
        progress=(0, 2, ""), _dir="", _ts=0.0,
    )


class FakeSubprocess:
    """Stand-in for subprocess.run that records argv and writes an output file.

    Materializes the ffmpeg/yt-dlp output path (the last arg) so existence
    assertions and batch summaries are meaningful.
    """

    def __init__(self):
        self.calls = []

    def run(self, args, **kw):
        self.calls.append(list(args))
        if args and args[0] == "ffmpeg":
            out = args[-1]
            os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
            with open(out, "w") as f:
                f.write("audio-bytes")
        return subprocess.CompletedProcess(args, 0, b"", b"")


def fake_ytdl(ok_vid=None, fail_vid=None):
    """A yt-dlp mock. Writes the predicted flac file unless the vid is failing."""
    def fake(args, timeout=180, task=None):
        if "--write-thumbnail" in args:
            o = args[args.index("-o") + 1]
            os.makedirs(os.path.dirname(o), exist_ok=True)
            with open(o + ".jpg", "w") as f:
                f.write("cover")
            return 0, "", ""
        url = args[-1]
        vid = url.split("watch?v=")[-1]
        if fail_vid and vid == fail_vid:
            return 1, "", "yt-dlp: unable to download video"
        o = args[args.index("-o") + 1]
        flac = o.replace("%(ext)s", "flac")
        os.makedirs(os.path.dirname(flac), exist_ok=True)
        with open(flac, "w") as f:
            f.write("flacdata")
        return 0, "", ""
    return fake


def fake_ytdl_dump(song):
    """yt-dlp mock that also handles the /api/download --dump-single-json call."""
    def fake(args, timeout=180, task=None):
        if "--dump-single-json" in args:
            return 0, json.dumps(song), ""
        return fake_ytdl()(args, timeout=timeout, task=task)
    return fake


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    """Point config/state/library at a throwaway dir and clear live state."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(library, "LIBRARY_FILE", str(data_dir / "library.json"))
    state.tasks.clear()
    monkeypatch.setattr(state, "GLOBAL_SLOTS", threading.BoundedSemaphore(4))
    return str(data_dir)


@pytest.fixture
def patched_engine(app_env, monkeypatch):
    """Mock the engine's external deps (yt-dlp, MusicBrainz, subprocess)."""
    fake_run = FakeSubprocess()
    monkeypatch.setattr(engine, "run_ytdl", fake_ytdl())
    monkeypatch.setattr(engine, "search_musicbrainz", lambda a, t: None)
    monkeypatch.setattr(subprocess, "run", fake_run.run)
    return fake_run