"""Signed session tokens for the web cookie.

Deliberately tiny and stdlib-only: the payload is just "which user is this",
signed so the browser can't forge one. Nothing secret travels in it — the
Last.fm session key (a permanent write credential) never leaves the database.

Format: base64url(json).base64url(hmac_sha256). Any failure — malformed,
tampered, expired — verifies as None, i.e. signed out.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any

DEFAULT_MAX_AGE = 90 * 24 * 3600  # Last.fm session keys don't expire; ours do
COOKIE_NAME = "ds_session"

# The in-flight Last.fm request token, kept while the user is away approving us.
# Short-lived: it is only useful between "connect" and coming back.
PENDING_COOKIE_NAME = "ds_pending"
PENDING_MAX_AGE = 20 * 60


class SessionError(RuntimeError):
    """Raised when the app is not configured to sign sessions."""


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _secret() -> bytes:
    value = os.environ.get("SESSION_SECRET", "")
    if not value:
        raise SessionError(
            "SESSION_SECRET not set — generate one with "
            'python -c "import secrets; print(secrets.token_urlsafe(32))"'
        )
    return value.encode("utf-8")


def sign(payload: dict[str, Any], *, now: int | None = None) -> str:
    """Sign a payload into a cookie value. `iat` is stamped on."""
    body = _b64(
        json.dumps(
            {**payload, "iat": int(now if now is not None else time.time())},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    mac = _b64(hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{mac}"


def verify(
    token: str | None, *, max_age: int = DEFAULT_MAX_AGE, now: int | None = None
) -> dict[str, Any] | None:
    """The payload if the token is authentic and fresh, else None."""
    if not token or token.count(".") != 1:
        return None
    body, mac = token.split(".")

    try:
        expected = _b64(hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).digest())
    except SessionError:
        raise
    except Exception:
        return None
    if not hmac.compare_digest(mac, expected):
        return None

    try:
        payload = json.loads(_unb64(body))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None

    issued = payload.get("iat")
    if not isinstance(issued, int):
        return None
    current = int(now if now is not None else time.time())
    if current - issued > max_age or issued - current > 300:  # no future-dated tokens
        return None
    return payload
