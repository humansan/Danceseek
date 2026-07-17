"""Candidate gathering: run the searches, keep the top few results per platform.

No acceptance decisions here — the batched LLM picker judges. YouTube results
keep YouTube's own rank order (its search is good; the official upload is
usually at the top). Queries keep the remix/variant ("Extended Mix" etc.)
because platforms typically carry those exact variants.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class Unit:
    """One resolvable thing: a track row, a mashup component, or a mashup row."""

    artists: list[str]
    title: str | None
    remix: str | None
    raw_text: str
    kind: Literal["track", "component", "mashup_row"] = "track"


@dataclass
class UnitCandidates:
    unit: Unit
    spotify: list[dict[str, Any]] = field(default_factory=list)
    youtube: list[dict[str, Any]] = field(default_factory=list)
    lastfm: list[dict[str, Any]] = field(default_factory=list)
    log: dict[str, Any] = field(default_factory=dict)


def build_query(unit: Unit) -> str:
    """Primary query: artists + title + remix (variants stay in — platforms
    usually list the Extended Mix / named remix as its own entry)."""
    parts = [" ".join(unit.artists), unit.title or "", unit.remix or ""]
    return " ".join(p for p in parts if p).strip()


def gather(unit: Unit, clients, limit: int = 5) -> UnitCandidates:
    out = UnitCandidates(unit=unit)

    # --- YouTube: native rank order, one query
    if clients.youtube:
        query = unit.raw_text if unit.kind == "mashup_row" else build_query(unit)
        out.youtube = clients.youtube.search(query, limit=limit)
        out.log["youtube_query"] = query

    if unit.kind == "mashup_row":
        return out  # whole mashups: YouTube only

    # --- Spotify: primary query, one fallback without the remix if empty
    if clients.spotify:
        query = build_query(unit)
        out.spotify = clients.spotify.search(query, limit=limit)
        out.log["spotify_query"] = query
        if not out.spotify and unit.remix:
            fallback = build_query(Unit(unit.artists, unit.title, None, unit.raw_text))
            out.spotify = clients.spotify.search(fallback, limit=limit)
            out.log["spotify_query_fallback"] = fallback

    # --- Last.fm: direct canonical lookups + search->canonicalize, deduped
    if clients.lastfm and unit.title:
        primary = unit.artists[0] if unit.artists else ""
        seen: dict[tuple[str, str], dict[str, Any]] = {}

        def add(info: dict[str, Any] | None) -> None:
            if info:
                seen[(info["artist"].lower(), info["track"].lower())] = info

        if primary:
            if unit.remix:
                add(clients.lastfm.get_info(primary, f"{unit.title} ({unit.remix})"))
            add(clients.lastfm.get_info(primary, unit.title))
        if not seen:
            for m in clients.lastfm.search(unit.title, artist=primary or None, limit=limit)[:3]:
                add(clients.lastfm.get_info(m["artist"], m["track"]))
        # highest-listeners first: that ordering is itself a signal for the picker
        out.lastfm = sorted(seen.values(), key=lambda c: -c["listeners"])[:limit]
        out.log["lastfm_lookups"] = len(seen)

    return out
