"""Pydantic domain models.

The Setlist / SetlistTrack shapes mirror the future Postgres tables
(`Setlists`, `Setlist_Tracks`) from docs/v0 - 1001tl lists.md so the JSON
store can be migrated 1:1 later. `raw_text` is always preserved verbatim —
it is the key the future Last.fm scrobbler will map from.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, Field

SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Extraction stage output (deterministic, pre-LLM)
# ---------------------------------------------------------------------------


class RawTrackRow(BaseModel):
    """One tracklist row exactly as scraped from the page."""

    position: int
    cue_time: str | None = None  # e.g. "0:00", "1:02:30"; None if not listed
    raw_text: str  # verbatim, e.g. "Skrillex & Fred again.. - Rumble"
    is_played_with: bool = False  # row was marked "w/" (layered over previous)


class RawSetlistPage(BaseModel):
    """Everything the extractor could pull deterministically from the DOM."""

    source_url: str
    title: str | None = None
    dj_names: list[str] = Field(default_factory=list)
    event: str | None = None
    date_recorded: str | None = None  # ISO date string when parseable
    genres: list[str] = Field(default_factory=list)
    rows: list[RawTrackRow] = Field(default_factory=list)
    fallback_text: str | None = None  # page text when row selectors failed


# ---------------------------------------------------------------------------
# Normalization stage output (LLM structured output)
# ---------------------------------------------------------------------------


class MashupComponent(BaseModel):
    """One side of a mashup ("A vs. B")."""

    artists: list[str] = Field(default_factory=list)
    title: str | None = None


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
# Persisted record
# ---------------------------------------------------------------------------


class SetlistTrack(ParsedTrack):
    """A parsed track plus scrape context and the (future) resolution slot."""

    cue_time: str | None = None
    # Reserved for Step 2 (resolution agent): spotify_id, youtube_id, confidence...
    resolution: dict | None = None


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
    scraped_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    parser: ParserInfo
    tracks: list[SetlistTrack] = Field(default_factory=list)
