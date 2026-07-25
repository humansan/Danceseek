"""Batched LLM picker: one structured call chooses the best candidate (or none)
per platform for a whole batch of tracks.

Candidates are referenced by short keys (S1/Y2/L1) so the model can only pick
from what the searches returned — an invalid key is discarded, never stored.
"""

from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate
from langchain_openrouter import ChatOpenRouter
from pydantic import BaseModel, Field

from ..config import settings
from ..models import LastfmMatch, PlatformMatch, Resolution
from .gather import UnitCandidates

SYSTEM_PROMPT = """You match DJ-setlist tracks (parsed from 1001tracklists) to search results from Spotify, YouTube, and Last.fm. For each numbered track you get the parsed metadata and keyed candidates (S1..=Spotify, Y1..=YouTube, L1..=Last.fm). For each track, pick the best candidate key per platform, or null if none is a real match.

Rules:
- A match must be the same recording: same title and at least one shared artist. Similar-but-different tracks, other songs by the same artist, or covers are NOT matches.
- Version variants: if the track specifies a version ("Extended Mix", "Slowed", a named remix), prefer the candidate with that exact variant; the plain original is acceptable ONLY for generic variants like Extended Mix/Radio Edit. For a NAMED remix/edit (e.g. "Hamdi Remix"), a candidate without that remixer is NOT a match.
- YouTube candidates are in YouTube's own ranking order; prefer the highest-ranked official/plausible upload (official channels, topic channels, high-quality full uploads). Reject DJ sets, hour-long mixes, and shorts.
- Last.fm candidates are canonical entries sorted by listener count; prefer the highest-listeners entry that matches. Its exact spelling is the scrobble identity.
- Many DJ-set tracks are unreleased bootlegs/edits that exist on NO platform. Returning null for any or all platforms is a correct, common outcome. Never force a weak match.
- confidence: your certainty across the picks for that track (0-1). Below 0.75 the picks are discarded, so don't report matches you doubt.
- Answer with the candidate's KEY exactly as shown ("L1", "S2", "Y1") — never the candidate's title or artist text.
Return one entry per track, in the same order, echoing its number."""

USER_PROMPT = """Match these {count} tracks:

{blocks}"""


class UnitPick(BaseModel):
    unit: int = Field(description="The track's number, echoed back")
    spotify: str | None = Field(default=None, description="Chosen Spotify key (e.g. 'S2') or null")
    youtube: str | None = Field(default=None, description="Chosen YouTube key (e.g. 'Y1') or null")
    lastfm: str | None = Field(default=None, description="Chosen Last.fm key (e.g. 'L1') or null")
    confidence: float = Field(description="Certainty of these picks, 0.0-1.0")


class BatchPicks(BaseModel):
    picks: list[UnitPick] = Field(default_factory=list)


def _fmt_duration(ms: int | None) -> str:
    if not ms:
        return "?"
    s = ms // 1000
    return f"{s // 60}:{s % 60:02d}"


def _candidate_label(platform: str, c: dict) -> str:
    """The exact line the model sees for a candidate (minus its key)."""
    if platform == "spotify":
        return f"{', '.join(c['artists'])} - {c['title']} ({_fmt_duration(c['duration_ms'])})"
    if platform == "youtube":
        return f"{c['title']} [uploader: {c['artists'][0]}] ({_fmt_duration(c['duration_ms'])})"
    return f"{c['artist']} - {c['track']} (listeners={c['listeners']})"


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def _candidate_aliases(platform: str, c: dict) -> set[str]:
    """Strings that unambiguously name this candidate, for when the model
    answers with the candidate's text instead of its key."""
    if platform == "spotify":
        core = f"{', '.join(c['artists'])} - {c['title']}"
    elif platform == "youtube":
        core = c["title"]
    else:
        core = f"{c['artist']} - {c['track']}"
    return {_norm(_candidate_label(platform, c)), _norm(core)}


def _format_block(index: int, uc: UnitCandidates) -> str:
    u = uc.unit
    lines = [
        f"#{index} [{u.kind}] {u.raw_text}"
        f"  (parsed: artists={u.artists}, title={u.title!r}, version={u.remix!r})"
    ]
    for platform, candidates in (
        ("spotify", uc.spotify), ("youtube", uc.youtube), ("lastfm", uc.lastfm)
    ):
        letter = platform[0].upper() if platform != "lastfm" else "L"
        for i, c in enumerate(candidates, 1):
            lines.append(f"  {letter}{i}: {_candidate_label(platform, c)}")
    if len(lines) == 1:
        lines.append("  (no candidates on any platform)")
    return "\n".join(lines)


def _build_llm():
    llm = ChatOpenRouter(
        model=settings.llm_model, temperature=0, max_tokens=settings.llm_max_tokens
    )
    return llm.with_structured_output(BatchPicks, method="json_schema").with_retry(
        stop_after_attempt=3
    )


def _key_to_candidate(key: str | None, candidates: list, platform: str) -> dict | None:
    """Resolve the model's answer to one of the gathered candidates, or None.

    Accepts what small models actually emit, not just the documented form:
    'S2', a bare '2', 'S2: Artist - Title', or — the common drift — the
    candidate's own text with no key at all. Text is matched by exact
    (normalized) equality against the rendered candidate line, so this stays a
    strict identification: anything that doesn't name a gathered candidate is
    still discarded rather than guessed at.
    """
    if not key:
        return None
    text = key.strip()

    # Positional key: "L1", "1", or "L1: <label>".
    token = text.split(":", 1)[0].strip()
    digits = token[1:] if len(token) > 1 and token[0].isalpha() else token
    if digits.isdigit():
        idx = int(digits) - 1
        return candidates[idx] if 0 <= idx < len(candidates) else None

    # The model echoed the candidate instead of its key.
    target = _norm(text)
    for candidate in candidates:
        if target in _candidate_aliases(platform, candidate):
            return candidate
    return None


def apply_pick(
    uc: UnitCandidates, pick: UnitPick | None, applicable: list[str]
) -> Resolution:
    """Turn the model's keyed picks into a gated Resolution (pure, testable).

    `applicable`: platforms that were actually searchable for this unit
    (clients with missing keys don't count against "resolved")."""
    spotify = youtube = lastfm = None
    confidence = 0.0
    unparsed: list[str] = []
    if pick is not None and pick.confidence >= settings.resolve_min_confidence:
        confidence = round(min(pick.confidence, 1.0), 3)
        s = _key_to_candidate(pick.spotify, uc.spotify, "spotify")
        y = _key_to_candidate(pick.youtube, uc.youtube, "youtube")
        lf = _key_to_candidate(pick.lastfm, uc.lastfm, "lastfm")
        # A pick we couldn't tie to a candidate is dropped — but say so, so a
        # future change in how the model answers is visible instead of silent.
        unparsed = [
            f"{name}={answer!r}"
            for name, answer, resolved in (
                ("spotify", pick.spotify, s), ("youtube", pick.youtube, y), ("lastfm", pick.lastfm, lf)
            )
            if answer and resolved is None
        ]
        spotify = PlatformMatch(**s) if s else None
        youtube = PlatformMatch(**y) if y else None
        lastfm = (
            LastfmMatch(
                artist=lf["artist"], track=lf["track"], mbid=lf.get("mbid"),
                listeners=lf["listeners"], url=lf.get("url"),
            )
            if lf
            else None
        )

    matched = [p for p, m in (("spotify", spotify), ("lastfm", lastfm), ("youtube", youtube)) if m and p in applicable]
    if not matched:
        status = "no_match"
    elif len(matched) == len(applicable):
        status = "resolved"
    else:
        status = "partial"
    return Resolution(
        status=status, spotify=spotify, youtube=youtube, lastfm=lastfm,
        confidence=confidence if matched else 0.0, method="batch_llm",
        notes=("unmatched pick keys: " + ", ".join(unparsed)) if unparsed else None,
    )


def pick_batch(
    batch: list[UnitCandidates], applicable: list[list[str]]
) -> list[Resolution]:
    """One LLM call for the whole batch; returns one Resolution per unit."""
    blocks = "\n\n".join(_format_block(i + 1, uc) for i, uc in enumerate(batch))
    prompt = ChatPromptTemplate.from_messages([("system", SYSTEM_PROMPT), ("user", USER_PROMPT)])
    chain = prompt | _build_llm()
    result: BatchPicks = chain.invoke({"count": len(batch), "blocks": blocks})

    picks_by_unit = {p.unit: p for p in result.picks}
    return [
        apply_pick(uc, picks_by_unit.get(i + 1), applicable[i])
        for i, uc in enumerate(batch)
    ]
