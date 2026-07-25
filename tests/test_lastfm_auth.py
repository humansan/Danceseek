"""Last.fm authentication: the signature algorithm and the token→session trade.

The signature is the whole security of the write API — if it's wrong, nothing
scrobbles, and the failure mode is an opaque "invalid method signature".
"""

import pytest

from soundseek.lastfm import auth


@pytest.fixture(autouse=True)
def credentials(monkeypatch):
    monkeypatch.setenv("LASTFM_API_KEY", "xxxxxxxx")
    monkeypatch.setenv("LASTFM_SECRET", "SECRET")


# --- api_sig ----------------------------------------------------------------


def test_signature_matches_the_documented_construction():
    """md5 of the alphabetically sorted <name><value> pairs with the secret
    appended — here md5("api_keyxxxxxxxxmethodauth.getSessiontokenyyyyyyyySECRET")."""
    sig = auth.api_sig(
        {"api_key": "xxxxxxxx", "method": "auth.getSession", "token": "yyyyyyyy"}, "SECRET"
    )
    assert sig == "72b82e5dc285245f5dbbe7d06fac6368"


def test_parameter_insertion_order_does_not_matter():
    a = auth.api_sig({"token": "y", "api_key": "x", "method": "m"}, "s")
    b = auth.api_sig({"api_key": "x", "method": "m", "token": "y"}, "s")
    assert a == b


def test_format_and_callback_are_excluded_from_the_signature():
    bare = auth.api_sig({"api_key": "x", "method": "m"}, "s")
    with_transport = auth.api_sig(
        {"api_key": "x", "method": "m", "format": "json", "callback": "cb"}, "s"
    )
    assert bare == with_transport


def test_a_different_secret_gives_a_different_signature():
    assert auth.api_sig({"a": "1"}, "one") != auth.api_sig({"a": "1"}, "two")


def test_non_ascii_values_are_hashed_as_utf8():
    """Track and artist names carry non-Latin-1 characters (Ørjan); hashing them
    as anything but UTF-8 produces a signature Last.fm rejects."""
    sig = auth.api_sig(
        {"api_key": "k", "method": "track.scrobble", "track": "Ørjan Nilsen"}, "SECRET"
    )
    assert sig == "52f7321357d5c65600f2a03bb550ac1d"


# --- credentials ------------------------------------------------------------


def test_a_missing_secret_says_why_the_api_key_is_not_enough(monkeypatch):
    monkeypatch.delenv("LASTFM_SECRET", raising=False)
    with pytest.raises(auth.LastfmAuthError, match="read-only"):
        auth.credentials()


def test_a_missing_api_key_is_reported(monkeypatch):
    monkeypatch.delenv("LASTFM_API_KEY", raising=False)
    with pytest.raises(auth.LastfmAuthError, match="LASTFM_API_KEY"):
        auth.credentials()


# --- the flow ---------------------------------------------------------------


class _Response:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


def _post(monkeypatch, payload, status=200):
    sent = {}

    def fake_post(url, data=None, timeout=None):
        sent["url"] = url
        sent["data"] = data
        return _Response(payload, status)

    monkeypatch.setattr(auth.httpx, "post", fake_post)
    return sent


def test_get_token_returns_the_request_token(monkeypatch):
    sent = _post(monkeypatch, {"token": "tok-123"})
    assert auth.get_token() == "tok-123"
    assert sent["data"]["method"] == "auth.getToken"
    assert "api_sig" in sent["data"]  # even getToken is signed


def test_get_session_returns_username_and_key(monkeypatch):
    _post(monkeypatch, {"session": {"name": "ansdas", "key": "sk-abc"}})
    assert auth.get_session("tok") == ("ansdas", "sk-abc")


def test_a_lastfm_error_becomes_a_clear_exception(monkeypatch):
    _post(monkeypatch, {"error": 14, "message": "This token has not been authorized"})
    with pytest.raises(auth.LastfmAuthError, match="not been authorized"):
        auth.get_session("tok")


def test_an_empty_session_is_an_error_not_a_silent_none(monkeypatch):
    _post(monkeypatch, {"session": {}})
    with pytest.raises(auth.LastfmAuthError, match="no session"):
        auth.get_session("tok")


def test_auth_url_carries_the_key_token_and_callback():
    url = auth.auth_url("tok-9", "http://localhost:3000/api/auth/lastfm/callback")
    assert url.startswith("https://www.last.fm/api/auth/?")
    assert "api_key=xxxxxxxx" in url and "token=tok-9" in url
    assert "cb=http%3A%2F%2Flocalhost%3A3000%2Fapi%2Fauth%2Flastfm%2Fcallback" in url
