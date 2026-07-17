"""Pydantic domain models.

The Setlist / SetlistTrack shapes mirror the future Postgres tables
(`Setlists`, `Setlist_Tracks`) from docs/v0 - 1001tl lists.md so the JSON
store can be migrated 1:1 later. `raw_text` is always preserved verbatim —
it is the key the future Last.fm scrobbler will map from.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = 1


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Extraction stage output (deterministic, pre-LLM)
# ---------------------------------------------------------------------------


class RawTrackRow(BaseModel):
    """One tracklist row exactly as scraped from the page."""

    position: int  # our unique row index in page order (w/ rows included)
    source_track_number: int | None = None  # the number 1001TL displays; None on "w/" rows
    cue_time: str | None = None  # e.g. "0:00", "1:02:30"; None if not listed
    raw_text: str  # verbatim, e.g. "Skrillex & Fred again.. - Rumble"
    is_played_with: bool = False  # row was marked "w/" (layered over previous)
    # 1001TL sometimes lists a mashup's component tracks as explicit sub-rows
    # (chain-link rows, class tlpSubTog). They belong to this row, not the list.
    component_texts: list[str] = Field(default_factory=list)


class RawSetlistPage(BaseModel):
    """Everything the extractor could pull deterministically from the DOM."""

    source_url: str
    title: str | None = None
    dj_names: list[str] = Field(default_factory=list)
    event: str | None = None
    date_recorded: str | None = None  # ISO date string when parseable
    genres: list[str] = Field(default_factory=list)
    # The set recording the page's cue timestamps refer to (best available link)
    media_url: str | None = None
    media_kind: str | None = None  # "youtube" | "soundcloud" | "hearthis" | "other"
    rows: list[RawTrackRow] = Field(default_factory=list)
    fallback_text: str | None = None  # page text when row selectors failed


# ---------------------------------------------------------------------------
# Normalization stage output (LLM structured output)
# ---------------------------------------------------------------------------


class MashupComponent(BaseModel):
    """One component track of a mashup."""

    artists: list[str] = Field(default_factory=list)
    title: str | None = None
    remix: str | None = None
    # Filled by Step 2: each component resolves to its own platform track.
    resolution: "Resolution | None" = None


class ParsedTrack(BaseModel):
    """A single normalized track row."""

    position: int = Field(description="Track position, echoed from the input row")
    raw_text: str = Field(description="The input raw_text echoed back VERBATIM")
    artists: list[str] = Field(
        default_factory=list,
        description="Artist names split out (e.g. 'A & B' -> ['A', 'B']). Empty for ID tracks.",
    )
    title: str | None = Field(
        default=None, description="Track title without remix/version suffix. None for ID tracks."
    )
    remix: str | None = Field(
        default=None,
        description="Remix/version/edit descriptor, e.g. 'Extended Mix', 'Fred again.. Remix'",
    )
    is_id: bool = Field(
        default=False,
        description="True if the track is unreleased/unidentified ('ID - ID' or artist ID)",
    )
    played_with: int | None = Field(
        default=None,
        description="Position of the track this row is layered over ('w/' rows), else null",
    )
    mashup_components: list[MashupComponent] = Field(
        default_factory=list,
        description="For mashups ('A vs. B'), the component tracks; empty otherwise",
    )


class ParsedTracklist(BaseModel):
    """LLM output: the full normalized tracklist."""

    tracks: list[ParsedTrack] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Resolution (Step 2): platform matches + canonical track registry
# ---------------------------------------------------------------------------


class PlatformMatch(BaseModel):
    """One accepted Spotify track / YouTube video match."""

    id: str  # Spotify track id / YouTube video id
    title: str
    artists: list[str] = Field(default_factory=list)  # uploader for YouTube
    url: str
    duration_ms: int | None = None


class LastfmMatch(BaseModel):
    """The canonical Last.fm identity — these exact strings are the future
    scrobble payload (Last.fm's highest-scrobbled entry, typically 1 artist)."""

    artist: str
    track: str
    mbid: str | None = None
    listeners: int = 0
    url: str | None = None


ResolutionStatus = Literal["resolved", "partial", "no_match", "unreleased"]


class Resolution(BaseModel):
    """Outcome of resolving one track/component against the platforms.

    Precision over recall: a platform field is populated ONLY when the match
    cleared the confidence threshold. Empty fields on bootlegs/edits are the
    correct outcome, not a failure ("no_match"). A null Resolution slot on a
    track means resolution was never attempted.
    """

    status: ResolutionStatus
    track_id: str | None = None  # canonical registry track (data/tracks.json)
    spotify: PlatformMatch | None = None
    youtube: PlatformMatch | None = None
    lastfm: LastfmMatch | None = None
    confidence: float = 0.0
    # "cascade"/"agent" are legacy values kept so old records stay loadable
    method: Literal["batch_llm", "registry", "skip", "cascade", "agent"] = "batch_llm"
    resolved_at: str = Field(default_factory=_utcnow)
    notes: str | None = None


class TrackRecord(BaseModel):
    """A row in the canonical track registry (the future `Tracks` table)."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    artists: list[str] = Field(default_factory=list)
    title: str | None = None
    remix: str | None = None
    is_unreleased: bool = False
    spotify_id: str | None = None
    youtube_id: str | None = None
    lastfm_artist: str | None = None
    lastfm_track: str | None = None
    mbid: str | None = None
    created_at: str = Field(default_factory=_utcnow)
    updated_at: str = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# Persisted record
# ---------------------------------------------------------------------------


class SetlistTrack(ParsedTrack):
    """A parsed track plus scrape context and the (future) resolution slot."""

    source_track_number: int | None = None  # 1001TL's displayed number; None on "w/" rows
    cue_time: str | None = None
    # Filled by Step 2 (resolution): platform matches + registry track id.
    resolution: Resolution | None = None


class ParserInfo(BaseModel):
    model: str
    schema_version: int = SCHEMA_VERSION


class Setlist(BaseModel):
    """The persisted setlist record (future `Setlists` + `Setlist_Tracks` rows)."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source: str = "1001tracklists"
    source_url: str
    title: str | None = None
    dj_names: list[str] = Field(default_factory=list)
    event: str | None = None
    date_recorded: str | None = None
    genres: list[str] = Field(default_factory=list)
    media_url: str | None = None  # linked set recording (cue timestamps refer to it)
    media_kind: str | None = None
    scraped_at: str = Field(default_factory=_utcnow)
    parser: ParserInfo
    tracks: list[SetlistTrack] = Field(default_factory=list)
