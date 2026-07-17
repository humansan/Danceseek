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
_MAX_RETRIES = 4  # playlistItems.insert returns spurious 409/503s
_RETRY_BACKOFF = 1.0  # seconds, multiplied by attempt number


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

    def _insert_one(self, playlist_id: str, vid: str) -> httpx.Response:
        return self._http.post(
            f"{_API}/playlistItems",
            params={"part": "snippet"},
            json={
                "snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {"kind": "youtube#video", "videoId": vid},
                }
            },
        )

    def add_videos(self, playlist_id: str, video_ids: list[str]) -> tuple[int, list[tuple[str, str]]]:
        """Insert each video, one call apiece. Returns (added, failures) where a
        failure is (video_id, reason). The endpoint returns spurious 409/503s, so
        those are retried; a genuinely bad video is skipped, not fatal."""
        added = 0
        failures: list[tuple[str, str]] = []
        for i, vid in enumerate(video_ids):
            quota_hit = False
            for attempt in range(_MAX_RETRIES):
                resp = self._insert_one(playlist_id, vid)
                if resp.status_code == 200:
                    added += 1
                    break
                if resp.status_code == 403 and "quota" in resp.text.lower():
                    quota_hit = True
                    break
                # 409/503/500 are transient on this endpoint — back off and retry.
                if resp.status_code in (409, 500, 503) and attempt < _MAX_RETRIES - 1:
                    time.sleep(_RETRY_BACKOFF * (attempt + 1))
                    continue
                # Anything else (or exhausted retries): skip this video, keep going.
                failures.append((vid, f"{resp.status_code} {resp.reason_phrase}"))
                break
            if quota_hit:
                # Stop cleanly; mark the rest so the created playlist + URL survive.
                failures += [(v, "quota_exceeded") for v in video_ids[i:]]
                break
            time.sleep(settings.export_api_delay_seconds)
        return added, failures

    def export(self, plan: ExportPlan, name: str, description: str, public: bool) -> dict:
        playlist_id, url = self.create_playlist(name, description, public)
        added, failures = self.add_videos(playlist_id, [i.id for i in plan.items])
        return {"url": url, "added": added, "failed": failures}
