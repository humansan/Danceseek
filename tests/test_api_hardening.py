"""Deployment guards: config checks and rate limits."""

import pytest

pytest.importorskip("fastapi")

from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import apps.api.main as main  # noqa: E402
from soundseek import db, session  # noqa: E402
from soundseek.config import settings  # noqa: E402
from soundseek.lastfm import submit  # noqa: E402

client = TestClient(main.app)


# --- configuration report ----------------------------------------------------


def test_a_missing_database_url_is_fatal(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert any("DATABASE_URL" in line for line in main.config_report()["fatal"])


def test_missing_auth_secrets_only_warn(monkeypatch):
    """Browse must still serve with no Last.fm credentials — those endpoints
    already answer 503 on their own."""
    monkeypatch.setenv("DATABASE_URL", "postgres://x")
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    monkeypatch.delenv("LASTFM_SECRET", raising=False)
    report = main.config_report()

    assert report["fatal"] == []
    assert any("SESSION_SECRET" in line for line in report["warn"])
    assert any("LASTFM" in line for line in report["warn"])


def test_a_localhost_web_url_warns_in_a_deployment(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://x")
    monkeypatch.setattr(settings, "web_url", "http://localhost:3000")
    assert any("SOUNDSEEK_WEB_URL" in line for line in main.config_report()["warn"])


def test_a_configured_web_url_does_not_warn(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://x")
    monkeypatch.setenv("SESSION_SECRET", "s")
    monkeypatch.setenv("LASTFM_API_KEY", "k")
    monkeypatch.setenv("LASTFM_SECRET", "s")
    monkeypatch.setattr(settings, "web_url", "https://danceseek.example")
    assert main.config_report() == {"fatal": [], "warn": []}


# --- the limiter itself ------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_limiter():
    main._HITS.clear()
    yield
    main._HITS.clear()


def test_the_limiter_allows_up_to_the_cap_then_refuses():
    for _ in range(3):
        main.rate_limit("k", limit=3, window_s=60)
    with pytest.raises(HTTPException) as e:
        main.rate_limit("k", limit=3, window_s=60)
    assert e.value.status_code == 429
    assert "Retry-After" in e.value.headers


def test_the_window_slides(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(main.time, "monotonic", lambda: clock[0])
    for _ in range(3):
        main.rate_limit("k", limit=3, window_s=60)

    clock[0] += 61  # everything ages out
    main.rate_limit("k", limit=3, window_s=60)  # must not raise


def test_limits_are_per_user_not_global():
    for _ in range(3):
        main.rate_limit("scrobble:alice", limit=3, window_s=60)
    main.rate_limit("scrobble:bob", limit=3, window_s=60)  # unaffected


# --- applied to the endpoints that spend Last.fm quota ------------------------


@pytest.fixture
def signed_in(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setattr(db, "session_key_for", lambda uid: "sk")
    monkeypatch.setattr(db, "get_scrobble_config", lambda uid: {})
    monkeypatch.setattr(main, "_windows_for_user", lambda *a, **k: _windows())
    monkeypatch.setattr(submit, "scrobble", lambda key, plays: submit.SubmitResult(accepted=1))
    client.cookies.set(session.COOKIE_NAME, session.sign({"uid": "user-1"}))
    yield
    client.cookies.clear()


def _windows():
    from soundseek.scrobble.windows import CueWindow, WindowSet

    return WindowSet(
        setlist_id="s", timing="cue", live_capable=True, duration_s=600,
        windows=[
            CueWindow(
                position=1, start_s=0, end_s=300, timing="cue", label="A – T",
                scrobble_artist="A", scrobble_track="T",
            )
        ],
    )


def test_whole_set_scrobbling_is_tightly_limited(signed_in):
    """Each call can submit 50+ plays, so this cap is deliberately low."""
    body = {"duration": 600}
    for _ in range(10):
        assert client.post("/setlists/s/scrobble-set", json=body).status_code == 200
    over = client.post("/setlists/s/scrobble-set", json=body)

    assert over.status_code == 429
    assert over.headers.get("Retry-After")


def test_single_scrobbles_have_headroom_for_a_real_set(signed_in):
    """A dense set is about one scrobble a minute — the cap must not bite."""
    target = {"setlist_id": "s", "position": 1}
    for _ in range(60):
        assert client.post("/scrobble", json=target).status_code == 200
    assert client.post("/scrobble", json=target).status_code == 429
