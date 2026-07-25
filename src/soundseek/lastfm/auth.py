"""Last.fm web authentication.

The three-step desktop/web flow:

  1. auth.getToken            -> an unauthorized request token
  2. send the user to last.fm/api/auth/?api_key=…&token=…&cb=…
  3. auth.getSession(token)   -> (username, session key)

The session key does not expire and authorizes scrobbling on that user's
behalf forever, so it is stored server-side and never sent to a browser.

Every call here is signed with the shared secret; the API key alone is
read-only, which is why resolution works without any of this.
"""

from __future__ import annotations

import hashlib
import os
from urllib.parse import urlencode

import httpx

API_URL = "https://ws.audioscrobbler.com/2.0/"
AUTH_URL = "https://www.last.fm/api/auth/"
_TIMEOUT = 15.0


class LastfmAuthError(RuntimeError):
    """Missing credentials, or Last.fm refused the request."""


def api_sig(params: dict[str, str], secret: str) -> str:
    """The `api_sig` for a call: md5 of the sorted <name><value> pairs + secret.

    `format` and `callback` are excluded per the spec — they describe the
    transport, not the call.
    """
    joined = "".join(
        f"{key}{params[key]}" for key in sorted(params) if key not in ("format", "callback")
    )
    return hashlib.md5((joined + secret).encode("utf-8")).hexdigest()


def credentials() -> tuple[str, str]:
    """(api_key, shared_secret) — raises with a pointed message if unset."""
    key = os.environ.get("LASTFM_API_KEY", "").strip()
    secret = os.environ.get("LASTFM_SECRET", "").strip()
    if not key:
        raise LastfmAuthError("LASTFM_API_KEY not set in .env")
    if not secret:
        raise LastfmAuthError(
            "LASTFM_SECRET not set in .env — the API key alone is read-only. "
            "Both values are shown on the Account Created page at "
            "https://www.last.fm/api/account/create"
        )
    return key, secret


def signed_call(method: str, **params: str) -> dict:
    """POST a signed call to Last.fm. Shared by auth and scrobble submission."""
    key, secret = credentials()
    payload = {"method": method, "api_key": key, **params}
    payload["api_sig"] = api_sig(payload, secret)
    payload["format"] = "json"

    try:
        response = httpx.post(API_URL, data=payload, timeout=_TIMEOUT)
    except httpx.HTTPError as e:
        raise LastfmAuthError(f"Could not reach Last.fm: {e}") from e

    try:
        body = response.json()
    except ValueError as e:
        raise LastfmAuthError(f"Last.fm {method} returned non-JSON ({response.status_code})") from e

    if isinstance(body, dict) and body.get("error"):
        raise LastfmAuthError(f"Last.fm {method} failed ({body['error']}): {body.get('message')}")
    if response.status_code != 200:
        raise LastfmAuthError(f"Last.fm {method} failed ({response.status_code})")
    return body


def get_token() -> str:
    """Step 1: an unauthorized request token."""
    token = signed_call("auth.getToken").get("token")
    if not token:
        raise LastfmAuthError("Last.fm returned no token")
    return token


def auth_url(token: str, callback: str) -> str:
    """Step 2: where to send the user to approve us."""
    key, _ = credentials()
    return f"{AUTH_URL}?{urlencode({'api_key': key, 'token': token, 'cb': callback})}"


def get_session(token: str) -> tuple[str, str]:
    """Step 3: trade an approved token for (username, session_key).

    Fails if the user declined or never approved — Last.fm reports that as
    error 14 (token not authorized) or 4 (invalid token).
    """
    session = signed_call("auth.getSession", token=token).get("session") or {}
    username, key = session.get("name"), session.get("key")
    if not username or not key:
        raise LastfmAuthError("Last.fm returned no session")
    return username, key
