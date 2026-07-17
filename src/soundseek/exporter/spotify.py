"""Create a Spotify playlist and add resolved tracks (user OAuth token)."""

from __future__ import annotations

import httpx

from .collect import ExportPlan
from .oauth import authorize

_API = "https://api.spotify.com/v1"
_TIMEOUT = httpx.Timeout(20.0)


class SpotifyExporter:
    def __init__(self) -> None:
        self._token = authorize("spotify")
        self._http = httpx.Client(
            timeout=_TIMEOUT, headers={"Authorization": f"Bearer {self._token}"}
        )

    def _user_id(self) -> str:
        resp = self._http.get(f"{_API}/me")
        resp.raise_for_status()
        return resp.json()["id"]

    def create_playlist(self, name: str, description: str, public: bool) -> tuple[str, str]:
        """Create an empty playlist; return (playlist_id, url)."""
        user_id = self._user_id()
        resp = self._http.post(
            f"{_API}/users/{user_id}/playlists",
            json={"name": name, "description": description, "public": public},
        )
        resp.raise_for_status()
        data = resp.json()
        return data["id"], data["external_urls"]["spotify"]

    def add_tracks(self, playlist_id: str, track_ids: list[str]) -> int:
        """Add tracks in batches of 100 (API limit). Returns count added."""
        added = 0
        for start in range(0, len(track_ids), 100):
            uris = [f"spotify:track:{tid}" for tid in track_ids[start : start + 100]]
            resp = self._http.post(f"{_API}/playlists/{playlist_id}/tracks", json={"uris": uris})
            resp.raise_for_status()
            added += len(uris)
        return added

    def export(self, plan: ExportPlan, name: str, description: str, public: bool) -> dict:
        playlist_id, url = self.create_playlist(name, description, public)
        added = self.add_tracks(playlist_id, [i.id for i in plan.items])
        return {"url": url, "added": added}
