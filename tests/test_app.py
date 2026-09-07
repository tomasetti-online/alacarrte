"""App-level tests: routes, request helpers, rate limiting, retry gate.

slugify/clean_title live in engine; mutable state (tasks, rate_map) in state;
tunables (ADMIN_IPS, RATE_LIMIT) in config. is_admin/is_channel_url were folded
into check_rate / api_channel during the architecture pass.
"""
import pytest

import app
import config
import engine
import state

client = app.app.test_client()


# --- Routes / smoke tests ---------------------------------------------------

def test_health_ok():
    resp = client.get("/api/health")
    assert resp.status_code == 200


def test_index_serves_ui():
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"ALAC" in resp.data or b"ALACarrte" in resp.data


# --- Pure helpers (engine) --------------------------------------------------

def test_slugify_strips_illegal_fs_chars():
    assert engine.slugify("A/B:C*D?\"<E>|F") == "ABCDEF"


def test_slugify_caps_at_120():
    assert len(engine.slugify("x" * 500)) == 120


def test_clean_title_removes_official_suffixes():
    assert engine.clean_title("Song (Official Music Video)") == "Song"
    assert engine.clean_title("Song [Official Audio]") == "Song"
    assert engine.clean_title("Song (Official Lyric Video)") == "Song"


def test_clean_title_drops_lyrics_link_and_tail():
    assert engine.clean_title("Song with lyrics download link https://x") == "Song"
    assert engine.clean_title("Song   ---") == "Song"


# --- Rate limiting ----------------------------------------------------------

def test_check_rate_lan_bypasses():
    assert app.check_rate("10.0.0.5") == (True, 0)


def test_check_rate_limits_public_ip(monkeypatch):
    monkeypatch.setattr(state, "rate_map", {})
    ip = "8.8.8.8"
    assert app.check_rate(ip, max_per_min=3)[0] is True
    assert app.check_rate(ip, max_per_min=3)[0] is True
    assert app.check_rate(ip, max_per_min=3)[0] is True
    allowed, wait = app.check_rate(ip, max_per_min=3)
    assert allowed is False
    assert wait >= 1


# --- Retry gate (the 2026-09-06 zombie fix) ---------------------------------

def _task(status, tmp_path, track_error=False, track_done=False):
    return {
        "status": status,
        "tracks": [{"done": track_done, "error": track_error, "path": None}],
        "_dir": str(tmp_path),
    }


def test_retry_rejects_inflight_task(monkeypatch, tmp_path):
    monkeypatch.setattr(state, "tasks", {"t1": _task("downloading", tmp_path=tmp_path)})
    resp = client.post("/api/retry-track/t1/0")
    assert resp.status_code == 409


def test_retry_rejects_track_not_failed(monkeypatch, tmp_path):
    monkeypatch.setattr(state, "tasks", {"t1": _task("done", track_done=True, tmp_path=tmp_path)})
    resp = client.post("/api/retry-track/t1/0")
    assert resp.status_code == 409


def test_retry_unknown_task_404(monkeypatch):
    monkeypatch.setattr(state, "tasks", {})
    resp = client.post("/api/retry-track/nope/0")
    assert resp.status_code == 404


def test_retry_allowed_for_failed_track(monkeypatch, tmp_path):
    monkeypatch.setattr(state, "tasks", {"t1": _task("done", track_error=True, tmp_path=tmp_path)})
    monkeypatch.setattr(engine, "process_track", lambda *a, **k: None)  # no real download in tests
    resp = client.post("/api/retry-track/t1/0")
    assert resp.status_code == 200
