"""Canonical track registry: data/tracks.json (the future `Tracks` table).

Every confidently-resolved unit mints (or reuses) a TrackRecord here. Lookup
runs BEFORE any platform searching — DJ sets share tracks heavily, so a
cross-set cache hit skips a whole resolution run.

Dedupe key priority: spotify_id > lastfm canonical pair > normalized parsed fields.
"""

from __future__ import annotations

import json

from .config import settings
from .models import Resolution, TrackRecord, _utcnow
from .resolver.scoring import normalize


def _load() -> dict[str, TrackRecord]:
    if settings.tracks_path.exists():
        raw = json.loads(settings.tracks_path.read_text(encoding="utf-8"))
        return {tid: TrackRecord.model_validate(rec) for tid, rec in raw.items()}
    return {}


def _save(tracks: dict[str, TrackRecord]) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.tracks_path.write_text(
        json.dumps(
            {tid: rec.model_dump() for tid, rec in tracks.items()},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def parsed_key(artists: list[str], title: str | None, remix: str | None) -> str | None:
    """Normalized fallback identity from parsed 1001TL fields."""
    if not title:
        return None
    artist_part = " ".join(sorted(normalize(a) for a in artists))
    return f"{artist_part}|{normalize(title)}|{normalize(remix or '')}"


class Registry:
    def __init__(self) -> None:
        self._tracks = _load()
        self._by_spotify: dict[str, str] = {}
        self._by_lastfm: dict[tuple[str, str], str] = {}
        self._by_parsed: dict[str, str] = {}
        for tid, rec in self._tracks.items():
            self._index(tid, rec)

    def _index(self, tid: str, rec: TrackRecord) -> None:
        if rec.spotify_id:
            self._by_spotify[rec.spotify_id] = tid
        if rec.lastfm_artist and rec.lastfm_track:
            self._by_lastfm[(rec.lastfm_artist.lower(), rec.lastfm_track.lower())] = tid
        key = parsed_key(rec.artists, rec.title, rec.remix)
        if key:
            self._by_parsed[key] = tid

    def get(self, track_id: str) -> TrackRecord | None:
        return self._tracks.get(track_id)

    def lookup(
        self, artists: list[str], title: str | None, remix: str | None
    ) -> TrackRecord | None:
        """Find an existing record by parsed fields (pre-search cache check)."""
        key = parsed_key(artists, title, remix)
        tid = self._by_parsed.get(key) if key else None
        return self._tracks.get(tid) if tid else None

    def find_or_create(
        self,
        artists: list[str],
        title: str | None,
        remix: str | None,
        resolution: Resolution,
    ) -> TrackRecord:
        """Mint or update the canonical record for a resolved unit."""
        tid = None
        if resolution.spotify:
            tid = self._by_spotify.get(resolution.spotify.id)
        if tid is None and resolution.lastfm:
            tid = self._by_lastfm.get(
                (resolution.lastfm.artist.lower(), resolution.lastfm.track.lower())
            )
        if tid is None:
            key = parsed_key(artists, title, remix)
            tid = self._by_parsed.get(key) if key else None

        if tid is None:
            rec = TrackRecord(
                artists=artists,
                title=title,
                remix=remix,
                is_unreleased=resolution.status == "unreleased",
            )
            self._tracks[rec.id] = rec
        else:
            rec = self._tracks[tid]

        # Enrich with any newly-confident platform identities (never overwrite
        # an existing id with a different one — first confident match wins).
        if resolution.spotify and not rec.spotify_id:
            rec.spotify_id = resolution.spotify.id
        if resolution.youtube and not rec.youtube_id:
            rec.youtube_id = resolution.youtube.id
        if resolution.lastfm and not rec.lastfm_artist:
            rec.lastfm_artist = resolution.lastfm.artist
            rec.lastfm_track = resolution.lastfm.track
            rec.mbid = resolution.lastfm.mbid
        rec.updated_at = _utcnow()
        self._index(rec.id, rec)
        _save(self._tracks)
        return rec

    def resolution_from_record(self, rec: TrackRecord) -> Resolution | None:
        """Rebuild a Resolution from a cached record (registry cache hit).

        Only platform *ids/names* survive the round trip; that's all later
        steps (export, scrobbling) need."""
        from .models import LastfmMatch, PlatformMatch

        if rec.is_unreleased:
            return Resolution(status="unreleased", track_id=rec.id, method="registry")
        if not (rec.spotify_id or rec.youtube_id or rec.lastfm_artist):
            return None  # record exists but has nothing useful cached
        return Resolution(
            status="resolved" if (rec.spotify_id and rec.lastfm_artist) else "partial",
            track_id=rec.id,
            spotify=PlatformMatch(
                id=rec.spotify_id,
                title=rec.title or "",
                artists=rec.artists,
                url=f"https://open.spotify.com/track/{rec.spotify_id}",
            )
            if rec.spotify_id
            else None,
            youtube=PlatformMatch(
                id=rec.youtube_id,
                title=rec.title or "",
                artists=rec.artists,
                url=f"https://www.youtube.com/watch?v={rec.youtube_id}",
            )
            if rec.youtube_id
            else None,
            lastfm=LastfmMatch(artist=rec.lastfm_artist, track=rec.lastfm_track or "")
            if rec.lastfm_artist
            else None,
            confidence=1.0,  # was accepted once; the registry is trusted
            method="registry",
        )
