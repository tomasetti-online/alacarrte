import pytest

import app as alacarrte

client = alacarrte.app.test_client()


# --- Routes / smoke tests ---------------------------------------------------

def test_health_ok():
    resp = client.get("/api/health")
    assert resp.status_code == 200


def test_index_serves_ui():
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"ALAC" in resp.data or b"ALACarrte" in resp.data


# --- Pure helpers -----------------------------------------------------------

def test_slugify_strips_illegal_fs_chars():
    assert alacarrte.slugify("A/B:C*D?\"<E>|F") == "ABCDEF"


def test_slugify_caps_at_120():
    assert len(alacarrte.slugify("x" * 500)) == 120


def test_clean_title_removes_official_suffixes():
    assert alacarrte.clean_title("Song (Official Music Video)") == "Song"
    assert alacarrte.clean_title("Song [Official Audio]") == "Song"
    assert alacarrte.clean_title("Song (Official Lyric Video)") == "Song"


def test_clean_title_drops_lyrics_link_and_tail():
    assert alacarrte.clean_title("Song with lyrics download link https://x") == "Song"
    assert alacarrte.clean_title("Song   ---") == "Song"


def test_is_channel_url_true_for_channel_forms():
    assert alacarrte.is_channel_url("https://youtube.com/@SomeArtist")
    assert alacarrte.is_channel_url("https://youtube.com/channel/UC1234")
    assert alacarrte.is_channel_url("https://youtube.com/c/ArtistName")


def test_is_channel_url_false_for_video():
    assert not alacarrte.is_channel_url("https://www.youtube.com/watch?v=abc123")


def test_is_admin_honors_admin_ips(monkeypatch):
    monkeypatch.setattr(alacarrte, "ADMIN_IPS", {"1.2.3.4"})
    assert alacarrte.is_admin("1.2.3.4")
    assert not alacarrte.is_admin("5.6.7.8")


# --- Rate limiting ----------------------------------------------------------

def test_check_rate_lan_bypasses():
    assert alacarrte.check_rate("10.0.0.5") == (True, 0)


def test_check_rate_limits_public_ip(monkeypatch):
    monkeypatch.setattr(alacarrte, "rate_map", {})
    monkeypatch.setattr(alacarrte, "RATE_LIMIT", 3)
    ip = "8.8.8.8"
    assert alacarrte.check_rate(ip)[0] is True
    assert alacarrte.check_rate(ip)[0] is True
    assert alacarrte.check_rate(ip)[0] is True
    allowed, wait = alacarrte.check_rate(ip)
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
    monkeypatch.setattr(alacarrte, "tasks", {"t1": _task("downloading", tmp_path=tmp_path)})
    resp = client.post("/api/retry-track/t1/0")
    assert resp.status_code == 409


def test_retry_rejects_track_not_failed(monkeypatch, tmp_path):
    monkeypatch.setattr(alacarrte, "tasks", {"t1": _task("done", track_done=True, tmp_path=tmp_path)})
    resp = client.post("/api/retry-track/t1/0")
    assert resp.status_code == 409


def test_retry_unknown_task_404(monkeypatch):
    monkeypatch.setattr(alacarrte, "tasks", {})
    resp = client.post("/api/retry-track/nope/0")
    assert resp.status_code == 404


def test_retry_allowed_for_failed_track(monkeypatch, tmp_path):
    monkeypatch.setattr(alacarrte, "tasks", {"t1": _task("done", track_error=True, tmp_path=tmp_path)})
    monkeypatch.setattr(alacarrte, "process_track", lambda *a, **k: None)  # no real download in tests
    resp = client.post("/api/retry-track/t1/0")
    assert resp.status_code == 200
