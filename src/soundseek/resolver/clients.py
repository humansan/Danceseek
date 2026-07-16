"""Platform search clients: Spotify (client-credentials), Last.fm, YouTube (yt-dlp).

Each client returns plain candidate dicts; scoring/acceptance lives in
cascade.py. Clients never decide what a "good" match is.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

_TIMEOUT = httpx.Timeout(15.0)


class ClientError(RuntimeError):
    """Configuration or API failure in a platform client."""


# ---------------------------------------------------------------------------
# Spotify
# ---------------------------------------------------------------------------


class SpotifyClient:
    """Search-only Spotify access via the client-credentials flow."""

    TOKEN_URL = "https://accounts.spotify.com/api/token"
    SEARCH_URL = "https://api.spotify.com/v1/search"

    def __init__(self) -> None:
        self._client_id = os.environ.get("SPOTIFY_CLIENT_ID", "")
        self._client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
        if not self._client_id or not self._client_secret:
            raise ClientError("SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET not set in .env")
        self._http = httpx.Client(timeout=_TIMEOUT)
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    def _get_token(self) -> str:
        if self._token and time.monotonic() < self._token_expires_at - 60:
            return self._token
        resp = self._http.post(
            self.TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(self._client_id, self._client_secret),
        )
        if resp.status_code != 200:
            raise ClientError(f"Spotify token request failed ({resp.status_code}): {resp.text[:200]}")
        payload = resp.json()
        self._token = payload["access_token"]
        self._token_expires_at = time.monotonic() + payload.get("expires_in", 3600)
        return self._token

    def search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        """Return track candidates: {id, title, artists, url, duration_ms}."""
        resp = self._http.get(
            self.SEARCH_URL,
            params={"q": query, "type": "track", "limit": limit},
            headers={"Authorization": f"Bearer {self._get_token()}"},
        )
        if resp.status_code == 429:
            retry = int(resp.headers.get("Retry-After", "2"))
            time.sleep(min(retry, 10))
            return self.search(query, limit)
        if resp.status_code != 200:
            raise ClientError(f"Spotify search failed ({resp.status_code}): {resp.text[:200]}")
        items = resp.json().get("tracks", {}).get("items", [])
        return [
            {
                "id": t["id"],
                "title": t["name"],
                "artists": [a["name"] for a in t.get("artists", [])],
                "url": t.get("external_urls", {}).get("spotify", ""),
                "duration_ms": t.get("duration_ms"),
            }
            for t in items
        ]


# ---------------------------------------------------------------------------
# Last.fm
# ---------------------------------------------------------------------------


class LastfmClient:
    """Last.fm track lookup. get_info(autocorrect=1) maps spelling variants to
    the canonical entry; search() finds candidates when the direct hit misses."""

    BASE_URL = "https://ws.audioscrobbler.com/2.0/"

    def __init__(self) -> None:
        self._api_key = os.environ.get("LASTFM_API_KEY", "")
        if not self._api_key:
            raise ClientError("LASTFM_API_KEY not set in .env")
        self._http = httpx.Client(timeout=_TIMEOUT)

    def _call(self, method: str, **params: str) -> dict[str, Any]:
        resp = self._http.get(
            self.BASE_URL,
            params={"method": method, "api_key": self._api_key, "format": "json", **params},
        )
        if resp.status_code != 200:
            raise ClientError(f"Last.fm {method} failed ({resp.status_code}): {resp.text[:200]}")
        return resp.json()

    def get_info(self, artist: str, track: str) -> dict[str, Any] | None:
        """Canonical entry for (artist, track), or None if Last.fm doesn't know it.

        Returns {artist, track, mbid, listeners, playcount, url} with Last.fm's
        canonical spelling (autocorrect=1) — these strings are the scrobble names.
        """
        data = self._call("track.getInfo", artist=artist, track=track, autocorrect="1")
        info = data.get("track")
        if not info:
            return None  # {"error": 6, "message": "Track not found"}
        return {
            "artist": info.get("artist", {}).get("name", artist),
            "track": info.get("name", track),
            "mbid": info.get("mbid") or None,
            "listeners": int(info.get("listeners") or 0),
            "playcount": int(info.get("playcount") or 0),
            "url": info.get("url"),
        }

    def search(self, track: str, artist: str | None = None, limit: int = 8) -> list[dict[str, Any]]:
        """Track search candidates: {artist, track, listeners, url}."""
        params: dict[str, str] = {"track": track, "limit": str(limit)}
        if artist:
            params["artist"] = artist
        data = self._call("track.search", **params)
        matches = data.get("results", {}).get("trackmatches", {}).get("track", [])
        if isinstance(matches, dict):  # single result comes back as an object
            matches = [matches]
        return [
            {
                "artist": m.get("artist", ""),
                "track": m.get("name", ""),
                "listeners": int(m.get("listeners") or 0),
                "url": m.get("url"),
            }
            for m in matches
        ]


# ---------------------------------------------------------------------------
# YouTube (yt-dlp search — no API key, no quota)
# ---------------------------------------------------------------------------


class YouTubeSearch:
    def search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        """Video candidates: {id, title, artists(=[uploader]), url, duration_ms}."""
        from yt_dlp import YoutubeDL  # heavy import, keep lazy

        opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,  # metadata only, never touch the media
            "skip_download": True,
        }
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
        entries = (info or {}).get("entries", []) or []
        results = []
        for e in entries:
            if not e or not e.get("id"):
                continue
            duration = e.get("duration")
            results.append(
                {
                    "id": e["id"],
                    "title": e.get("title", ""),
                    "artists": [e.get("uploader") or e.get("channel") or ""],
                    "url": e.get("url") or f"https://www.youtube.com/watch?v={e['id']}",
                    "duration_ms": int(duration * 1000) if duration else None,
                }
            )
        return results
