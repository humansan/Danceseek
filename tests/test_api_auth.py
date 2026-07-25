"""Auth endpoints: sign-in flow, cookie handling, and what must never leak."""

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

import apps.api.main as main  # noqa: E402
from soundseek import db, session  # noqa: E402
from soundseek.lastfm import auth as lastfm_auth  # noqa: E402

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def secrets(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setenv("LASTFM_API_KEY", "key")
    monkeypatch.setenv("LASTFM_SECRET", "secret")


@pytest.fixture
def signed_in(monkeypatch):
    monkeypatch.setattr(
        db, "get_user", lambda uid: {"id": uid, "lastfm_username": "ansdas", "created_at": None}
    )
    client.cookies.set(session.COOKIE_NAME, session.sign({"uid": "user-1"}))
    yield
    client.cookies.clear()


# --- /me --------------------------------------------------------------------


def test_me_is_signed_out_without_a_cookie():
    client.cookies.clear()
    body = client.get("/me").json()
    assert body == {"lastfm_username": None, "connected": False, "pending": False}


def test_me_reports_the_connected_account(signed_in):
    assert client.get("/me").json() == {
        "lastfm_username": "ansdas",
        "connected": True,
        "pending": False,
    }


def test_a_forged_cookie_does_not_sign_anyone_in(monkeypatch):
    monkeypatch.setattr(db, "get_user", lambda uid: pytest.fail("must not reach the database"))
    client.cookies.set(session.COOKIE_NAME, "forged.token")
    try:
        assert client.get("/me").json()["connected"] is False
    finally:
        client.cookies.clear()


def test_a_valid_cookie_for_a_deleted_user_is_signed_out(monkeypatch):
    monkeypatch.setattr(db, "get_user", lambda uid: None)
    client.cookies.set(session.COOKIE_NAME, session.sign({"uid": "gone"}))
    try:
        assert client.get("/me").json()["connected"] is False
    finally:
        client.cookies.clear()


# --- the sign-in flow -------------------------------------------------------


def test_start_redirects_to_lastfm(monkeypatch):
    monkeypatch.setattr(lastfm_auth, "get_token", lambda: "tok-1")
    r = client.get("/auth/lastfm/start", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"].startswith("https://www.last.fm/api/auth/?")
    assert "token=tok-1" in r.headers["location"]


def test_start_reports_a_configuration_problem_rather_than_crashing(monkeypatch):
    def boom():
        raise lastfm_auth.LastfmAuthError("LASTFM_SECRET not set in .env")

    monkeypatch.setattr(lastfm_auth, "get_token", boom)
    r = client.get("/auth/lastfm/start", follow_redirects=False)
    assert r.status_code == 503 and "LASTFM_SECRET" in r.json()["detail"]


def test_callback_stores_the_session_and_sets_a_cookie(monkeypatch):
    stored = {}
    monkeypatch.setattr(lastfm_auth, "get_session", lambda t: ("ansdas", "sk-secret"))
    monkeypatch.setattr(
        db, "upsert_user",
        lambda username, key: (stored.update(username=username, key=key), "user-9")[1],
    )
    client.cookies.clear()

    r = client.get("/auth/lastfm/callback?token=tok", follow_redirects=False)
    assert r.status_code == 303 and "lastfm=connected" in r.headers["location"]
    assert stored == {"username": "ansdas", "key": "sk-secret"}

    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie.replace("Lax", "lax")
    # The cookie names the user; the Last.fm key never travels.
    assert "sk-secret" not in cookie
    assert session.verify(r.cookies[session.COOKIE_NAME])["uid"] == "user-9"


def test_a_declined_authorization_redirects_without_a_session(monkeypatch):
    def refuse(token):
        raise lastfm_auth.LastfmAuthError("This token has not been authorized")

    monkeypatch.setattr(lastfm_auth, "get_session", refuse)
    client.cookies.clear()
    r = client.get("/auth/lastfm/callback?token=tok", follow_redirects=False)

    assert r.status_code == 303 and "lastfm=failed" in r.headers["location"]
    assert session.COOKIE_NAME not in r.cookies
    # Last.fm's own wording is not echoed to the browser.
    assert "authorized" not in r.headers["location"]


def test_callback_without_a_token_is_treated_as_denied():
    client.cookies.clear()
    r = client.get("/auth/lastfm/callback", follow_redirects=False)
    assert r.status_code == 303 and "lastfm=denied" in r.headers["location"]


# --- completing when Last.fm never calls back -------------------------------


def test_start_stashes_the_token_so_the_flow_can_finish_without_a_callback(monkeypatch):
    monkeypatch.setattr(lastfm_auth, "get_token", lambda: "tok-pending")
    client.cookies.clear()
    r = client.get("/auth/lastfm/start", follow_redirects=False)

    stashed = session.verify(r.cookies[session.PENDING_COOKIE_NAME])
    assert stashed["tok"] == "tok-pending"
    client.cookies.clear()


def test_me_reports_a_connection_in_flight():
    client.cookies.set(session.PENDING_COOKIE_NAME, session.sign({"tok": "t"}))
    try:
        body = client.get("/me").json()
        assert body["connected"] is False and body["pending"] is True
    finally:
        client.cookies.clear()


def test_complete_redeems_the_stashed_token(monkeypatch):
    monkeypatch.setattr(lastfm_auth, "get_session", lambda t: ("ansdas", "sk"))
    monkeypatch.setattr(db, "upsert_user", lambda u, k: "user-3")
    client.cookies.set(session.PENDING_COOKIE_NAME, session.sign({"tok": "tok-approved"}))
    try:
        r = client.post("/auth/lastfm/complete")
        assert r.status_code == 200
        assert r.json() == {"lastfm_username": "ansdas", "connected": True, "pending": False}
        assert session.verify(r.cookies[session.COOKIE_NAME])["uid"] == "user-3"
    finally:
        client.cookies.clear()


def test_complete_without_a_pending_connection_is_a_conflict():
    client.cookies.clear()
    assert client.post("/auth/lastfm/complete").status_code == 409


def test_complete_before_the_user_approves_says_so(monkeypatch):
    def unapproved(token):
        raise lastfm_auth.LastfmAuthError("This token has not been authorized")

    monkeypatch.setattr(lastfm_auth, "get_session", unapproved)
    client.cookies.set(session.PENDING_COOKIE_NAME, session.sign({"tok": "t"}))
    try:
        r = client.post("/auth/lastfm/complete")
        assert r.status_code == 409 and "not approved" in r.json()["detail"]
        # The dead token must be dropped, or every page load retries it.
        assert "ds_pending=" in r.headers["set-cookie"]
        assert "Max-Age=0" in r.headers["set-cookie"] or "expires=" in r.headers["set-cookie"].lower()
    finally:
        client.cookies.clear()


def test_the_callback_falls_back_to_the_stashed_token(monkeypatch):
    """Some Last.fm setups redirect back without the token query param."""
    monkeypatch.setattr(lastfm_auth, "get_session", lambda t: ("ansdas", "sk"))
    monkeypatch.setattr(db, "upsert_user", lambda u, k: "user-4")
    client.cookies.set(session.PENDING_COOKIE_NAME, session.sign({"tok": "stashed"}))
    try:
        r = client.get("/auth/lastfm/callback", follow_redirects=False)
        assert "lastfm=connected" in r.headers["location"]
        assert session.verify(r.cookies[session.COOKIE_NAME])["uid"] == "user-4"
    finally:
        client.cookies.clear()


def test_logout_clears_the_cookie(signed_in):
    r = client.post("/auth/logout")
    assert r.status_code == 204
    assert 'ds_session=""' in r.headers["set-cookie"] or "ds_session=;" in r.headers["set-cookie"]


# --- leak guards ------------------------------------------------------------


def test_the_public_user_record_cannot_carry_the_session_key():
    """get_user selects three columns; the key is only reachable through the
    separate session_key_for(), which no endpoint calls."""
    import inspect

    source = inspect.getsource(db.get_user)
    assert "lastfm_session_key" not in source
    assert "SELECT id, lastfm_username, created_at" in source


def test_no_endpoint_reads_the_session_key():
    import inspect

    assert "session_key_for" not in inspect.getsource(main)
