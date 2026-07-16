"""LLM agent fallback for ambiguous resolutions.

Invoked only when the cascade lands in the ambiguous band. The agent can
reformulate queries and evaluate results, but it can only *pick from what the
tools returned*: every chosen ID/name is validated against the candidates
collected during the run, and anything else is discarded (anti-hallucination).
Concluding "not on these platforms" is a valid, successful outcome.
"""

from __future__ import annotations

import json
from typing import Any

from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_openrouter import ChatOpenRouter
from pydantic import BaseModel, Field

from ..config import settings
from ..models import LastfmMatch, PlatformMatch
from .cascade import CascadeResult, Unit

SYSTEM_PROMPT = """You are a music metadata resolver for DJ setlist tracks scraped from 1001tracklists. A deterministic matcher already tried and got ambiguous results; your job is to settle it.

You have a hard budget of 4 tool calls total. Spend them wisely, then conclude — an answer with nulls is always better than running out of budget.

Search strategy:
- If a search returns nothing useful, reformulate ONCE: drop qualifiers like "Extended Mix", try fewer artists, or try the remixer as the artist. Then conclude with what you have.
- For a named remix/edit (e.g. "Hamdi Remix"), the match MUST be that version — the plain original is WRONG. If only the original exists, report no match for that platform.
- Check artist lists: a high-confidence match shares the title and at least one artist.
- Last.fm: prefer the entry with the most listeners (that's the canonical scrobble entry). Its artist/track spelling is authoritative — report it exactly as returned.
- YouTube: prefer official uploads or high-quality full-track uploads; a DJ set or hour-long video is not a match for a single track.

CRITICAL RULES:
- Only report IDs and names that literally appeared in tool results this session. Never invent or recall IDs from memory.
- Many DJ-set tracks are unreleased bootlegs/edits that exist NOWHERE. Concluding "no match" for any or all platforms is a correct, successful answer. Do NOT force a bad match.
- Set confidence honestly: 0.9+ only for exact title+artist matches, below 0.75 means you are not sure (the match will be discarded)."""


class AgentPick(BaseModel):
    """The agent's final verdict for one track."""

    spotify_id: str | None = Field(default=None, description="Chosen Spotify track id, or null if no confident match")
    youtube_id: str | None = Field(default=None, description="Chosen YouTube video id, or null")
    lastfm_artist: str | None = Field(default=None, description="Canonical Last.fm artist name exactly as a tool returned it, or null")
    lastfm_track: str | None = Field(default=None, description="Canonical Last.fm track name exactly as a tool returned it, or null")
    confidence: float = Field(description="Your confidence in the reported matches, 0.0-1.0")
    note: str = Field(description="One sentence: what you concluded and why")


def refine_with_agent(unit: Unit, cascade: CascadeResult, clients) -> CascadeResult:
    """Run the agent on an ambiguous unit; returns an updated CascadeResult."""
    # Everything the tools return is collected here; picks must come from it.
    seen_spotify: dict[str, dict[str, Any]] = {}
    seen_youtube: dict[str, dict[str, Any]] = {}
    seen_lastfm: dict[tuple[str, str], dict[str, Any]] = {}

    @tool
    def search_spotify(query: str) -> str:
        """Search Spotify for tracks. Returns candidates with id, title, artists, duration."""
        if not clients.spotify:
            return "Spotify is not available."
        results = clients.spotify.search(query, limit=5)
        for c in results:
            seen_spotify[c["id"]] = c
        return json.dumps(
            [{k: c[k] for k in ("id", "title", "artists", "duration_ms")} for c in results],
            ensure_ascii=False,
        ) or "[]"

    @tool
    def search_lastfm(artist: str, track: str) -> str:
        """Look up a track on Last.fm. Returns the canonical entry (artist, track, listeners) plus search candidates."""
        if not clients.lastfm:
            return "Last.fm is not available."
        out: dict[str, Any] = {}
        info = clients.lastfm.get_info(artist, track)
        if info:
            seen_lastfm[(info["artist"], info["track"])] = info
            out["canonical"] = info
        found = clients.lastfm.search(track, artist=artist or None, limit=5)
        for m in found[:3]:
            canon = clients.lastfm.get_info(m["artist"], m["track"])
            if canon:
                seen_lastfm[(canon["artist"], canon["track"])] = canon
        out["candidates"] = [
            {"artist": a, "track": t, "listeners": c["listeners"]}
            for (a, t), c in seen_lastfm.items()
        ]
        return json.dumps(out, ensure_ascii=False)

    @tool
    def search_youtube(query: str) -> str:
        """Search YouTube. Returns candidates with id, title, uploader, duration."""
        if not clients.youtube:
            return "YouTube is not available."
        results = clients.youtube.search(query, limit=5)
        for c in results:
            seen_youtube[c["id"]] = c
        return json.dumps(
            [
                {"id": c["id"], "title": c["title"], "uploader": c["artists"][0], "duration_ms": c["duration_ms"]}
                for c in results
            ],
            ensure_ascii=False,
        ) or "[]"

    llm = ChatOpenRouter(
        model=settings.llm_model, temperature=0, max_tokens=settings.agent_max_tokens
    )
    agent = create_agent(
        llm,
        tools=[search_spotify, search_lastfm, search_youtube],
        system_prompt=SYSTEM_PROMPT,
        response_format=AgentPick,
    )

    context = {
        "raw_text": unit.raw_text,
        "parsed": {"artists": unit.artists, "title": unit.title, "remix": unit.remix},
        "kind": unit.kind,
        "cascade_best_scores": cascade.scores,
        "platforms_to_settle": "youtube only" if unit.kind == "mashup_row" else "spotify, lastfm, youtube",
    }
    result = agent.invoke(
        {"messages": [("user", f"Resolve this track:\n{json.dumps(context, ensure_ascii=False)}")]},
        config={"recursion_limit": settings.agent_max_iterations * 2 + 1},
    )
    pick: AgentPick = result["structured_response"]

    # --- validation: picks must exist in collected tool output and be confident
    updated = CascadeResult(
        spotify=cascade.spotify, youtube=cascade.youtube, lastfm=cascade.lastfm,
        scores=dict(cascade.scores), ambiguous=False, log=dict(cascade.log),
    )
    accepted = pick.confidence >= settings.resolve_min_confidence
    rejected: list[str] = []

    if accepted and pick.spotify_id:
        if pick.spotify_id in seen_spotify:
            updated.spotify = PlatformMatch(**seen_spotify[pick.spotify_id])
            updated.scores["spotify"] = round(pick.confidence, 3)
        else:
            rejected.append(f"spotify:{pick.spotify_id}")
    if accepted and pick.youtube_id:
        if pick.youtube_id in seen_youtube:
            updated.youtube = PlatformMatch(**seen_youtube[pick.youtube_id])
            updated.scores["youtube"] = round(pick.confidence, 3)
        else:
            rejected.append(f"youtube:{pick.youtube_id}")
    if accepted and pick.lastfm_artist and pick.lastfm_track:
        key = (pick.lastfm_artist, pick.lastfm_track)
        if key in seen_lastfm:
            c = seen_lastfm[key]
            updated.lastfm = LastfmMatch(
                artist=c["artist"], track=c["track"], mbid=c.get("mbid"),
                listeners=c["listeners"], url=c.get("url"),
            )
            updated.scores["lastfm"] = round(pick.confidence, 3)
        else:
            rejected.append(f"lastfm:{pick.lastfm_artist} - {pick.lastfm_track}")

    updated.log["agent"] = {
        "pick": pick.model_dump(),
        "rejected_hallucinated": rejected,
        "tools_seen": {
            "spotify": len(seen_spotify), "youtube": len(seen_youtube), "lastfm": len(seen_lastfm),
        },
    }
    return updated
