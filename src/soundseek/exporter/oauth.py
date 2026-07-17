"""OAuth 2.0 Authorization-Code flow with a local loopback redirect.

One-time browser login per provider; the refresh token is cached to
`data/auth/<provider>_token.json` so later runs get a fresh access token
silently. Missing client credentials raise OAuthError, which the exporter
turns into a warning rather than a crash (mirrors resolver/clients.py).
"""

from __future__ import annotations

import base64
import json
import os
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx

from ..config import settings

_TIMEOUT = httpx.Timeout(20.0)


class OAuthError(RuntimeError):
    """Missing OAuth configuration or a failed token exchange."""


@dataclass(frozen=True)
class Provider:
    name: str
    auth_url: str
    token_url: str
    scope: str
    client_id_env: str
    client_secret_env: str
    extra_auth_params: dict[str, str]


PROVIDERS = {
    "spotify": Provider(
        name="spotify",
        auth_url="https://accounts.spotify.com/authorize",
        token_url="https://accounts.spotify.com/api/token",
        scope="playlist-modify-public playlist-modify-private",
        client_id_env="SPOTIFY_CLIENT_ID",
        client_secret_env="SPOTIFY_CLIENT_SECRET",
        extra_auth_params={},
    ),
    "google": Provider(
        name="google",
        auth_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        scope="https://www.googleapis.com/auth/youtube",
        client_id_env="GOOGLE_CLIENT_ID",
        client_secret_env="GOOGLE_CLIENT_SECRET",
        # offline + consent so Google returns a refresh_token
        extra_auth_params={"access_type": "offline", "prompt": "consent"},
    ),
}


def _redirect_uri() -> str:
    return f"http://127.0.0.1:{settings.export_redirect_port}/callback"


def _creds(provider: Provider) -> tuple[str, str]:
    cid = os.environ.get(provider.client_id_env, "")
    secret = os.environ.get(provider.client_secret_env, "")
    if not cid or not secret:
        raise OAuthError(
            f"{provider.client_id_env} / {provider.client_secret_env} not set in .env"
        )
    return cid, secret


def _token_path(provider: Provider):
    return settings.auth_dir / f"{provider.name}_token.json"


def _load_cache(provider: Provider) -> dict | None:
    path = _token_path(provider)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _save_cache(provider: Provider, data: dict) -> None:
    settings.auth_dir.mkdir(parents=True, exist_ok=True)
    _token_path(provider).write_text(json.dumps(data, indent=2), encoding="utf-8")


class _CodeCatcher(BaseHTTPRequestHandler):
    code: str | None = None
    error: str | None = None

    def do_GET(self):  # noqa: N802 (http.server API)
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _CodeCatcher.code = params.get("code", [None])[0]
        _CodeCatcher.error = params.get("error", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        msg = "SoundSeek: authorization complete — you can close this tab."
        self.wfile.write(f"<html><body><h3>{msg}</h3></body></html>".encode())

    def log_message(self, *args):  # silence the default stderr logging
        pass


def _run_login(provider: Provider, client_id: str) -> str:
    """Open the browser, catch the redirect, return the authorization code."""
    _CodeCatcher.code = None
    _CodeCatcher.error = None
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": _redirect_uri(),
        "scope": provider.scope,
        **provider.extra_auth_params,
    }
    url = f"{provider.auth_url}?{urllib.parse.urlencode(params)}"

    server = HTTPServer(("127.0.0.1", settings.export_redirect_port), _CodeCatcher)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    print(f"Opening browser to authorize {provider.name}...")
    print(f"If it doesn't open, visit:\n{url}")
    webbrowser.open(url)

    deadline = time.monotonic() + 300
    while thread.is_alive() and time.monotonic() < deadline:
        thread.join(timeout=0.5)
    server.server_close()

    if _CodeCatcher.error:
        raise OAuthError(f"{provider.name} authorization denied: {_CodeCatcher.error}")
    if not _CodeCatcher.code:
        raise OAuthError(f"{provider.name} authorization timed out (no code received)")
    return _CodeCatcher.code


def _token_request(provider: Provider, client_id: str, client_secret: str, data: dict) -> dict:
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    payload = dict(data)
    if provider.name == "spotify":
        basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        headers["Authorization"] = f"Basic {basic}"
    else:  # google expects the creds in the body
        payload["client_id"] = client_id
        payload["client_secret"] = client_secret
    resp = httpx.post(provider.token_url, data=payload, headers=headers, timeout=_TIMEOUT)
    if resp.status_code != 200:
        raise OAuthError(f"{provider.name} token exchange failed ({resp.status_code}): {resp.text[:200]}")
    return resp.json()


def authorize(provider_name: str) -> str:
    """Return a valid access token, doing a browser login only if needed."""
    provider = PROVIDERS[provider_name]
    client_id, client_secret = _creds(provider)
    cache = _load_cache(provider)

    # Try silent refresh first.
    if cache and cache.get("refresh_token"):
        if time.time() < cache.get("expires_at", 0) - 60:
            return cache["access_token"]
        refreshed = _token_request(
            provider, client_id, client_secret,
            {"grant_type": "refresh_token", "refresh_token": cache["refresh_token"]},
        )
        cache["access_token"] = refreshed["access_token"]
        cache["expires_at"] = time.time() + refreshed.get("expires_in", 3600)
        # Google may or may not return a new refresh_token; keep the old one.
        if refreshed.get("refresh_token"):
            cache["refresh_token"] = refreshed["refresh_token"]
        _save_cache(provider, cache)
        return cache["access_token"]

    # First-time interactive login.
    code = _run_login(provider, client_id)
    token = _token_request(
        provider, client_id, client_secret,
        {"grant_type": "authorization_code", "code": code, "redirect_uri": _redirect_uri()},
    )
    cache = {
        "access_token": token["access_token"],
        "refresh_token": token.get("refresh_token"),
        "expires_at": time.time() + token.get("expires_in", 3600),
    }
    _save_cache(provider, cache)
    return cache["access_token"]
