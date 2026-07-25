"""Signed session cookies. A forged cookie must never authenticate anyone."""

import pytest

from soundseek import session


@pytest.fixture(autouse=True)
def secret(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-secret-value")


def test_round_trip_carries_the_payload():
    payload = session.verify(session.sign({"uid": "abc-123"}))
    assert payload["uid"] == "abc-123"
    assert isinstance(payload["iat"], int)


def test_a_tampered_payload_is_rejected():
    token = session.sign({"uid": "abc-123"})
    body, mac = token.split(".")
    forged = session._b64(b'{"iat":0,"uid":"someone-else"}') + "." + mac
    assert session.verify(forged) is None


def test_a_token_signed_with_another_secret_is_rejected(monkeypatch):
    token = session.sign({"uid": "abc-123"})
    monkeypatch.setenv("SESSION_SECRET", "a-different-secret")
    assert session.verify(token) is None


@pytest.mark.parametrize("token", [None, "", "garbage", "a.b.c", "onlyonepart", "!!.??"])
def test_malformed_tokens_verify_as_signed_out(token):
    assert session.verify(token) is None


def test_an_expired_token_is_rejected():
    token = session.sign({"uid": "u"}, now=1_000_000)
    assert session.verify(token, max_age=60, now=1_000_000 + 61) is None
    assert session.verify(token, max_age=60, now=1_000_000 + 59) is not None


def test_a_future_dated_token_is_rejected():
    """Clock skew is tolerated; a token minted far in the future is not."""
    token = session.sign({"uid": "u"}, now=2_000_000)
    assert session.verify(token, now=2_000_000 - 3600) is None
    assert session.verify(token, now=2_000_000 - 60) is not None


def test_signing_without_a_configured_secret_fails_loudly(monkeypatch):
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    with pytest.raises(session.SessionError, match="SESSION_SECRET"):
        session.sign({"uid": "u"})
