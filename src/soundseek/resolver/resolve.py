"""Resolution orchestrator: registry cache -> gather candidates -> batched LLM pick.

Flow per setlist:
1. Walk rows/components; is_id -> "unreleased" instantly; registry hits stamp
   cached resolutions; everything else becomes a pending Unit.
2. Gather top candidates per platform for each pending unit (searches only).
3. One LLM call per batch of units picks the best candidate (or none) per
   platform; picks are validated against the gathered candidates and gated by
   the confidence threshold. Empty results are valid (precision over recall).
4. Stamp resolutions, mint/enrich the registry, log everything.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Callable

from .. import store
from ..config import settings
from ..fetcher import url_digest
from ..models import Resolution, Setlist
from ..registry import get_registry
from .clients import ClientError, LastfmClient, SpotifyClient, YouTubeSearch
from .gather import Unit, UnitCandidates, gather
from .picker import pick_batch


@dataclass
class ResolveSummary:
    resolved: int = 0
    partial: int = 0
    no_match: int = 0
    unreleased: int = 0
    skipped: int = 0  # already had a resolution (no --force)
    registry_hits: int = 0
    llm_batches: int = 0
    warnings: list[str] = field(default_factory=list)

    def count(self, status: str) -> None:
        setattr(self, status, getattr(self, status) + 1)


def build_coverage(setlist: Setlist, summary: "ResolveSummary") -> dict:
    """A JSON-safe coverage summary for the setlists.coverage column / UI.

    Per-status counts come from the run summary; per-platform counts are
    scanned from the persisted resolutions (top-level tracks + mashup
    components), since one track can match on some platforms and not others.
    """
    spotify = youtube = lastfm = 0

    def tally(res: Resolution | None) -> None:
        nonlocal spotify, youtube, lastfm
        if res is None:
            return
        spotify += res.spotify is not None
        youtube += res.youtube is not None
        lastfm += res.lastfm is not None

    for track in setlist.tracks:
        tally(track.resolution)
        for component in track.mashup_components:
            tally(component.resolution)

    return {
        "total": summary.resolved + summary.partial + summary.no_match + summary.unreleased,
        "resolved": summary.resolved,
        "partial": summary.partial,
        "no_match": summary.no_match,
        "unreleased": summary.unreleased,
        "skipped": summary.skipped,
        "registry_hits": summary.registry_hits,
        "spotify": spotify,
        "youtube": youtube,
        "lastfm": lastfm,
    }


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

    def applicable(self, unit: Unit) -> list[str]:
        if unit.kind == "mashup_row":
            return ["youtube"] if self.youtube else []
        platforms = []
        if self.spotify:
            platforms.append("spotify")
        if self.lastfm:
            platforms.append("lastfm")
        if self.youtube:
            platforms.append("youtube")
        return platforms


@dataclass
class _Pending:
    """A unit awaiting resolution plus the setter that stamps it back."""

    unit: Unit
    stamp: Callable[[Resolution], None]


def _log_line(log_path, unit: Unit, resolution: Resolution, extra: dict) -> None:
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
        **extra,
    }
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def _collect_pending(
    setlist: Setlist,
    registry: Registry,
    summary: ResolveSummary,
    force: bool,
    limit: int | None,
    log_path,
) -> list[_Pending]:
    """Stamp instant outcomes (ids, registry hits); return units needing search."""
    pending: list[_Pending] = []
    processed_rows = 0

    def handle(unit: Unit, stamp: Callable[[Resolution], None]) -> None:
        if unit.kind != "mashup_row" and not force:
            cached = registry.lookup(unit.artists, unit.title, unit.remix)
            if cached is not None:
                rebuilt = registry.resolution_from_record(cached)
                if rebuilt is not None:
                    stamp(rebuilt)
                    summary.registry_hits += 1
                    summary.count(rebuilt.status)
                    _log_line(log_path, unit, rebuilt, {"registry_hit": cached.id})
                    return
        pending.append(_Pending(unit, stamp))

    for track in setlist.tracks:
        if limit is not None and processed_rows >= limit:
            break
        if track.resolution is not None and not force:
            summary.skipped += 1
            continue
        processed_rows += 1

        if track.is_id:
            track.resolution = Resolution(status="unreleased", method="skip", confidence=0.0)
            summary.count("unreleased")
            continue

        def stamp_track(res: Resolution, t=track) -> None:
            t.resolution = res

        if track.mashup_components:
            handle(
                Unit(track.artists, track.title, track.remix, track.raw_text, "mashup_row"),
                stamp_track,
            )
            for component in track.mashup_components:
                if not component.title and not component.artists:
                    component.resolution = Resolution(status="unreleased", method="skip")
                    summary.count("unreleased")
                    continue

                def stamp_component(res: Resolution, c=component) -> None:
                    c.resolution = res

                raw = f"{' & '.join(component.artists)} - {component.title or '?'}" + (
                    f" ({component.remix})" if component.remix else ""
                )
                handle(
                    Unit(component.artists, component.title, component.remix, raw, "component"),
                    stamp_component,
                )
        else:
            handle(Unit(track.artists, track.title, track.remix, track.raw_text), stamp_track)

    return pending


def resolve_setlist(
    setlist: Setlist,
    force: bool = False,
    limit: int | None = None,
) -> ResolveSummary:
    """Resolve all (or the first `limit`) unresolved rows; persists incrementally."""
    summary = ResolveSummary()
    clients = _Clients(summary)
    registry = get_registry()
    log_path = settings.resolution_logs_dir / f"{url_digest(setlist.source_url)}.jsonl"

    pending = _collect_pending(setlist, registry, summary, force, limit, log_path)

    # Phase 2: searches
    gathered: list[UnitCandidates] = []
    for p in pending:
        gathered.append(gather(p.unit, clients, limit=settings.resolve_candidates_per_platform))
        time.sleep(settings.resolve_api_delay_seconds)

    # Phase 3+4: batched LLM picks, stamp + registry + log + incremental saves
    batch_size = settings.resolve_batch_size
    for start in range(0, len(gathered), batch_size):
        batch = gathered[start : start + batch_size]
        batch_pending = pending[start : start + batch_size]
        applicable = [clients.applicable(uc.unit) for uc in batch]
        try:
            resolutions = pick_batch(batch, applicable)
            summary.llm_batches += 1
        except Exception as e:
            summary.warnings.append(f"LLM pick failed for batch at {start}: {e}")
            continue  # units stay unresolved (slot stays null) for a retry run

        for p, uc, resolution in zip(batch_pending, batch, resolutions):
            if p.unit.kind != "mashup_row" and (
                resolution.spotify or resolution.youtube or resolution.lastfm
            ):
                rec = registry.find_or_create(
                    p.unit.artists, p.unit.title, p.unit.remix, resolution
                )
                resolution.track_id = rec.id
            p.stamp(resolution)
            summary.count(resolution.status)
            _log_line(
                log_path,
                p.unit,
                resolution,
                {
                    "candidates": {
                        "spotify": [(c["id"], c["title"]) for c in uc.spotify],
                        "youtube": [(c["id"], c["title"]) for c in uc.youtube],
                        "lastfm": [f"{c['artist']} - {c['track']}" for c in uc.lastfm],
                    },
                    "queries": uc.log,
                },
            )
        store.save(setlist)  # crash-safe per batch

    store.save(setlist)

    # Auto-publish the processed setlist to the Neon cache table (best-effort).
    from .. import db

    try:
        db.push(setlist)
    except Exception as e:  # best-effort: a DB hiccup must never fail a resolve
        summary.warnings.append(f"Neon push skipped: {e}")

    return summary
