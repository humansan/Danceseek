"""Create a YouTube playlist and add resolved videos (Google OAuth token).

Uses the YouTube Data API (playlists.insert / playlistItems.insert). Each
insert costs ~50 quota units against the 10,000/day default — heavy use hits
the quota, so we go one video at a time with a small delay and stop cleanly on
a quota error, returning whatever was added.
"""

from __future__ import annotations

import time

import httpx

from ..config import settings
from .collect import ExportPlan
from .oauth import authorize

_API = "https://www.googleapis.com/youtube/v3"
_TIMEOUT = httpx.Timeout(20.0)


class YouTubeQuotaError(RuntimeError):
    pass


class YouTubeExporter:
    def __init__(self) -> None:
        self._token = authorize("google")
        self._http = httpx.Client(
            timeout=_TIMEOUT, headers={"Authorization": f"Bearer {self._token}"}
        )

    def create_playlist(self, name: str, description: str, public: bool) -> tuple[str, str]:
        resp = self._http.post(
            f"{_API}/playlists",
            params={"part": "snippet,status"},
            json={
                "snippet": {"title": name, "description": description},
                "status": {"privacyStatus": "public" if public else "private"},
            },
        )
        resp.raise_for_status()
        pid = resp.json()["id"]
        return pid, f"https://www.youtube.com/playlist?list={pid}"

    def add_videos(self, playlist_id: str, video_ids: list[str]) -> int:
        added = 0
        for vid in video_ids:
            resp = self._http.post(
                f"{_API}/playlistItems",
                params={"part": "snippet"},
                json={
                    "snippet": {
                        "playlistId": playlist_id,
                        "resourceId": {"kind": "youtube#video", "videoId": vid},
                    }
                },
            )
            if resp.status_code == 403 and "quota" in resp.text.lower():
                raise YouTubeQuotaError(
                    f"YouTube quota exhausted after adding {added} videos. "
                    "The playlist was created; retry later to add the rest."
                )
            resp.raise_for_status()
            added += 1
            time.sleep(settings.export_api_delay_seconds)
        return added

    def export(self, plan: ExportPlan, name: str, description: str, public: bool) -> dict:
        playlist_id, url = self.create_playlist(name, description, public)
        added = self.add_videos(playlist_id, [i.id for i in plan.items])
        return {"url": url, "added": added}
