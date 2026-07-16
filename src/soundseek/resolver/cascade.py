"""Deterministic resolution pass: query ladder + per-platform accept/reject.

Precision over recall: a platform match is returned ONLY if its score clears
`settings.resolve_min_confidence`. Bootlegs/edits that exist nowhere end with
empty results — that is a correct outcome. Borderline scores (within the
agent band) mark the unit as ambiguous so the agent can reformulate-or-rule-out.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from ..config import settings
from ..models import LastfmMatch, PlatformMatch
from . import scoring
from .clients import LastfmClient, SpotifyClient, YouTubeSearch


@dataclass
class Unit:
    """One resolvable thing: a track row, a mashup component, or a mashup row."""

    artists: list[str]
    title: str | None
    remix: str | None
    raw_text: str
    kind: Literal["track", "component", "mashup_row"] = "track"


@dataclass
class CascadeResult:
    spotify: PlatformMatch | None = None
    youtube: PlatformMatch | None = None
    lastfm: LastfmMatch | None = None
    scores: dict[str, float] = field(default_factory=dict)  # best score per platform
    ambiguous: bool = False  # something landed in the agent band
    log: dict[str, Any] = field(default_factory=dict)  # queries + candidates for debug

    @property
    def confidence(self) -> float:
        accepted = [
            s
            for platform, s in self.scores.items()
            if getattr(self, platform) is not None
        ]
        return round(min(accepted), 3) if accepted else 0.0


def build_queries(unit: Unit) -> list[str]:
    """Query ladder: full -> without generic qualifiers -> minimal."""
    artists = " ".join(unit.artists)
    title = unit.title or ""
    queries = []
    if unit.remix:
        queries.append(f"{artists} {title} {unit.remix}".strip())
    queries.append(f"{artists} {title}".strip())
    if unit.artists and len(unit.artists) > 1:
        queries.append(f"{unit.artists[0]} {title}".strip())
    # de-dupe preserving order
    seen: set[str] = set()
    return [q for q in queries if q and not (q in seen or seen.add(q))]


def _in_agent_band(score: float) -> bool:
    return settings.resolve_agent_band <= score < settings.resolve_min_confidence


def resolve_spotify(client: SpotifyClient, unit: Unit, result: CascadeResult) -> None:
    best: tuple[float, dict[str, Any]] | None = None
    attempts = []
    for query in build_queries(unit):
        candidates = client.search(query)
        scored = [
            (scoring.score_track_candidate(unit.artists, unit.title or "", unit.remix, c), c)
            for c in candidates
        ]
        attempts.append({"query": query, "candidates": [(round(s, 3), c["id"], c["title"]) for s, c in scored]})
        top = max(scored, default=None, key=lambda x: x[0])
        if top and (best is None or top[0] > best[0]):
            best = top
        if best and best[0] >= settings.resolve_min_confidence:
            break  # good enough, stop burning API calls
    result.log["spotify"] = attempts
    if best:
        score, cand = best
        result.scores["spotify"] = round(score, 3)
        if score >= settings.resolve_min_confidence:
            result.spotify = PlatformMatch(**cand)
        elif _in_agent_band(score):
            result.ambiguous = True


def resolve_lastfm(client: LastfmClient, unit: Unit, result: CascadeResult) -> None:
    """Canonicalization: direct getInfo (autocorrect) first, then search->getInfo,
    dedupe by canonical pair, pick highest listeners among acceptable matches."""
    title = unit.title or ""
    attempts: list[dict[str, Any]] = []
    candidates: dict[tuple[str, str], dict[str, Any]] = {}

    def try_get_info(artist: str, track: str) -> None:
        info = client.get_info(artist, track)
        attempts.append({"getInfo": [artist, track], "hit": bool(info)})
        if info:
            candidates[(info["artist"].lower(), info["track"].lower())] = info

    # 1. Direct canonical lookups (autocorrect fixes spelling variants).
    primary = unit.artists[0] if unit.artists else ""
    if primary and title:
        if unit.remix and not scoring.remix_is_generic(unit.remix):
            try_get_info(primary, f"{title} ({unit.remix})")
        try_get_info(primary, title)
        # A Spotify hit gives us platform-canonical names — reuse them.
        if result.spotify and not candidates:
            try_get_info(result.spotify.artists[0], result.spotify.title)

    # 2. Search fallback -> canonicalize each candidate via getInfo.
    if not candidates and title:
        found = client.search(title, artist=primary or None)
        attempts.append({"search": [primary, title], "results": len(found)})
        for m in found[:4]:
            try_get_info(m["artist"], m["track"])

    result.log["lastfm"] = attempts
    if not candidates:
        return

    scored = [
        (scoring.score_lastfm_candidate(unit.artists, title, unit.remix, c), c)
        for c in candidates.values()
    ]
    # Among acceptable candidates prefer the highest-listeners entry (the
    # "official" one users actually scrobble to); otherwise report best score.
    acceptable = [(s, c) for s, c in scored if s >= settings.resolve_min_confidence]
    if acceptable:
        score, cand = max(acceptable, key=lambda x: x[1]["listeners"])
        result.scores["lastfm"] = round(score, 3)
        result.lastfm = LastfmMatch(
            artist=cand["artist"],
            track=cand["track"],
            mbid=cand.get("mbid"),
            listeners=cand["listeners"],
            url=cand.get("url"),
        )
    else:
        best_score = max((s for s, _ in scored), default=0.0)
        result.scores["lastfm"] = round(best_score, 3)
        if _in_agent_band(best_score):
            result.ambiguous = True


def resolve_youtube(client: YouTubeSearch, unit: Unit, result: CascadeResult) -> None:
    # Mashup rows search the whole combined string (bootlegs live on YouTube
    # under names close to what 1001TL prints).
    query = unit.raw_text if unit.kind == "mashup_row" else build_queries(unit)[0]
    candidates = client.search(query)
    scored = [
        (
            scoring.score_youtube_candidate(
                unit.artists or [unit.raw_text], unit.title or unit.raw_text, unit.remix, c
            ),
            c,
        )
        for c in candidates
    ]
    result.log["youtube"] = [
        {"query": query, "candidates": [(round(s, 3), c["id"], c["title"]) for s, c in scored]}
    ]
    top = max(scored, default=None, key=lambda x: x[0])
    if top:
        score, cand = top
        result.scores["youtube"] = round(score, 3)
        if score >= settings.resolve_min_confidence:
            result.youtube = PlatformMatch(**cand)
        elif _in_agent_band(score):
            result.ambiguous = True


def run_cascade(
    unit: Unit,
    spotify: SpotifyClient | None,
    youtube: YouTubeSearch | None,
    lastfm: LastfmClient | None,
) -> CascadeResult:
    """Resolve one unit against the applicable platforms (None = skip)."""
    result = CascadeResult()
    if unit.kind == "mashup_row":
        # Whole mashups only exist as YouTube bootlegs; never on Spotify/Last.fm.
        if youtube:
            resolve_youtube(youtube, unit, result)
        return result
    if spotify:
        resolve_spotify(spotify, unit, result)
    if lastfm:
        resolve_lastfm(lastfm, unit, result)
    if youtube:
        resolve_youtube(youtube, unit, result)
    return result
