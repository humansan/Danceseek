"""Resolution orchestrator: route setlist rows through registry -> cascade -> agent.

Routing per row type (see plan):
- is_id rows/components: status "unreleased", zero API calls
- mashup rows: YouTube only (whole-mashup bootleg search); components get
  Spotify + Last.fm each
- everything else: Spotify + Last.fm + YouTube

Precision over recall throughout: empty platform slots are valid outcomes.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .. import store
from ..config import settings
from ..fetcher import url_digest
from ..models import Resolution, Setlist, SetlistTrack
from ..registry import Registry
from .cascade import CascadeResult, Unit, run_cascade
from .clients import ClientError, LastfmClient, SpotifyClient, YouTubeSearch


@dataclass
class ResolveSummary:
    resolved: int = 0
    partial: int = 0
    no_match: int = 0
    unreleased: int = 0
    skipped: int = 0  # already had a resolution (no --force)
    registry_hits: int = 0
    agent_runs: int = 0
    warnings: list[str] = field(default_factory=list)

    def count(self, status: str) -> None:
        setattr(self, status, getattr(self, status) + 1)


class _Clients:
    """Lazily-built platform clients; missing keys disable a platform with a warning."""

    def __init__(self, summary: ResolveSummary) -> None:
        self.spotify: SpotifyClient | None = None
        self.lastfm: LastfmClient | None = None
        self.youtube: YouTubeSearch | None = YouTubeSearch()
        try:
            self.spotify = SpotifyClient()
        except ClientError as e:
            summary.warnings.append(f"Spotify disabled: {e}")
        try:
            self.lastfm = LastfmClient()
        except ClientError as e:
            summary.warnings.append(f"Last.fm disabled: {e}")


def _status_for(result: CascadeResult, applicable: list[str]) -> str:
    matched = [p for p in applicable if getattr(result, p) is not None]
    if not matched:
        return "no_match"
    return "resolved" if len(matched) == len(applicable) else "partial"


def _applicable(unit: Unit, clients: _Clients) -> list[str]:
    if unit.kind == "mashup_row":
        return ["youtube"] if clients.youtube else []
    platforms = []
    if clients.spotify:
        platforms.append("spotify")
    if clients.lastfm:
        platforms.append("lastfm")
    if clients.youtube and unit.kind == "track":
        platforms.append("youtube")  # components skip YouTube (mashup row covers it)
    return platforms


def _log_line(log_path, unit: Unit, resolution: Resolution, cascade_log: dict[str, Any]) -> None:
    settings.resolution_logs_dir.mkdir(parents=True, exist_ok=True)
    line = {
        "raw_text": unit.raw_text,
        "kind": unit.kind,
        "status": resolution.status,
        "method": resolution.method,
        "confidence": resolution.confidence,
        "chosen": {
            "spotify": resolution.spotify.id if resolution.spotify else None,
            "youtube": resolution.youtube.id if resolution.youtube else None,
            "lastfm": f"{resolution.lastfm.artist} - {resolution.lastfm.track}"
            if resolution.lastfm
            else None,
        },
        "cascade": cascade_log,
    }
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def _resolve_unit(
    unit: Unit,
    clients: _Clients,
    registry: Registry,
    summary: ResolveSummary,
    use_agent: bool,
    log_path,
    force: bool = False,
) -> Resolution:
    # 1. Registry cache (skip for mashup rows — they aren't registry entities;
    #    --force bypasses the cache so a re-resolve actually re-searches)
    if unit.kind != "mashup_row" and not force:
        cached = registry.lookup(unit.artists, unit.title, unit.remix)
        if cached is not None:
            rebuilt = registry.resolution_from_record(cached)
            if rebuilt is not None:
                summary.registry_hits += 1
                summary.count(rebuilt.status)
                _log_line(log_path, unit, rebuilt, {"registry_hit": cached.id})
                return rebuilt

    # 2. Deterministic cascade
    applicable = _applicable(unit, clients)
    result = run_cascade(unit, clients.spotify, clients.youtube, clients.lastfm)
    method = "cascade"

    # 3. Agent fallback for the ambiguous band only
    if result.ambiguous and use_agent:
        from .agent import refine_with_agent  # lazy: langchain import

        try:
            result = refine_with_agent(unit, result, clients)
            method = "agent"
            summary.agent_runs += 1
        except Exception as e:  # agent failure must never lose the cascade outcome
            summary.warnings.append(f"agent failed on {unit.raw_text!r}: {e}")

    status = _status_for(result, applicable)
    resolution = Resolution(
        status=status,
        spotify=result.spotify,
        youtube=result.youtube,
        lastfm=result.lastfm,
        confidence=result.confidence,
        method=method,
    )

    # 4. Mint/enrich the registry for anything with at least one platform id
    if unit.kind != "mashup_row" and (result.spotify or result.youtube or result.lastfm):
        rec = registry.find_or_create(unit.artists, unit.title, unit.remix, resolution)
        resolution.track_id = rec.id

    summary.count(status)
    _log_line(log_path, unit, resolution, result.log)
    time.sleep(settings.resolve_api_delay_seconds)
    return resolution


def _resolve_track(
    track: SetlistTrack, resolve: Callable[[Unit], Resolution], summary: ResolveSummary
) -> None:
    """Fill resolution slots on one setlist row (and its mashup components)."""
    if track.is_id:
        track.resolution = Resolution(status="unreleased", method="skip", confidence=0.0)
        summary.count("unreleased")
        return

    if track.mashup_components:
        # the row itself: whole-mashup YouTube search
        track.resolution = resolve(
            Unit(
                artists=track.artists,
                title=track.title,
                remix=track.remix,
                raw_text=track.raw_text,
                kind="mashup_row",
            )
        )
        for component in track.mashup_components:
            if not component.title and not component.artists:
                component.resolution = Resolution(status="unreleased", method="skip")
                summary.count("unreleased")
                continue
            component.resolution = resolve(
                Unit(
                    artists=component.artists,
                    title=component.title,
                    remix=None,
                    raw_text=f"{' & '.join(component.artists)} - {component.title or 'ID'}",
                    kind="component",
                )
            )
        return

    track.resolution = resolve(
        Unit(
            artists=track.artists,
            title=track.title,
            remix=track.remix,
            raw_text=track.raw_text,
            kind="track",
        )
    )


def resolve_setlist(
    setlist: Setlist,
    force: bool = False,
    use_agent: bool = True,
    limit: int | None = None,
) -> ResolveSummary:
    """Resolve all (or the first `limit`) unresolved tracks; persists incrementally."""
    summary = ResolveSummary()
    clients = _Clients(summary)
    registry = Registry()
    log_path = settings.resolution_logs_dir / f"{url_digest(setlist.source_url)}.jsonl"

    def resolve(unit: Unit) -> Resolution:
        return _resolve_unit(unit, clients, registry, summary, use_agent, log_path, force=force)

    processed = 0
    since_save = 0
    for track in setlist.tracks:
        if limit is not None and processed >= limit:
            break
        if track.resolution is not None and not force:
            summary.skipped += 1
            continue
        _resolve_track(track, resolve, summary)
        processed += 1
        since_save += 1
        if since_save >= settings.resolve_save_every:
            store.save(setlist)  # crash-safe incremental persistence
            since_save = 0

    store.save(setlist)
    return summary
