"""Name normalization and candidate scoring for resolution.

Pure functions, stdlib only (difflib) — deliberately deterministic and
unit-testable. The confidence threshold that gates *storage* of a match
lives in config; this module only produces scores in [0, 1].
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from typing import Any

# Version qualifiers that platforms routinely drop from titles. A remix field
# consisting only of these words shouldn't be *required* to appear in a match.
GENERIC_QUALIFIERS = {
    "extended", "mix", "radio", "edit", "original", "version", "club",
    "instrumental", "intro", "rework", "vip", "dub",
}

_FEAT_RE = re.compile(r"\b(ft|feat|featuring|pres|presents)\b\.?", re.IGNORECASE)
_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Lowercase, strip accents/punctuation, unify '&'/'and', drop feat. markers."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().replace("&", " and ")
    text = _FEAT_RE.sub(" ", text)
    text = _PUNCT_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def similarity(a: str, b: str) -> float:
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def remix_is_generic(remix: str | None) -> bool:
    """True when the remix field carries no distinguishing name
    (e.g. 'Extended Mix'), so platforms may omit it entirely."""
    if not remix:
        return True
    return all(w in GENERIC_QUALIFIERS for w in normalize(remix).split())


def _title_variants(title: str, remix: str | None) -> list[str]:
    variants = [title]
    if remix:
        variants += [f"{title} ({remix})", f"{title} - {remix}"]
    return variants


def title_score(title: str, remix: str | None, candidate_title: str) -> float:
    return max(similarity(v, candidate_title) for v in _title_variants(title, remix))


def artist_overlap(ours: list[str], theirs: list[str]) -> float:
    """Fraction of our artists that appear (fuzzily) among the candidate's."""
    if not ours:
        return 0.0
    theirs_norm = [normalize(t) for t in theirs if t]
    joined = " ".join(theirs_norm)
    hits = 0
    for artist in ours:
        a = normalize(artist)
        if not a:
            continue
        if a in joined or any(similarity(artist, t) >= 0.85 for t in theirs if t):
            hits += 1
    return hits / len(ours)


def _remixer_present(remix: str | None, candidate: dict[str, Any]) -> bool:
    """For named remixes/edits ('Hamdi Remix'), the remixer must show up in the
    candidate's title or artist list — otherwise we'd match the wrong version."""
    if remix_is_generic(remix):
        return True
    haystack = normalize(candidate.get("title", "") + " " + " ".join(candidate.get("artists", [])))
    remixer_words = [w for w in normalize(remix or "").split() if w not in GENERIC_QUALIFIERS]
    return all(w in haystack for w in remixer_words)


def score_track_candidate(
    artists: list[str], title: str, remix: str | None, candidate: dict[str, Any]
) -> float:
    """Score a Spotify-style candidate ({title, artists}) against parsed fields."""
    t = title_score(title, remix, candidate.get("title", ""))
    a = artist_overlap(artists, candidate.get("artists", []))
    score = 0.6 * t + 0.4 * a
    if not _remixer_present(remix, candidate):
        score *= 0.5
    return score


def score_lastfm_candidate(
    artists: list[str], title: str, remix: str | None, candidate: dict[str, Any]
) -> float:
    """Score a Last.fm entry ({artist, track}). Last.fm entries usually carry a
    single artist, so any-of-ours matching replaces full overlap."""
    t = title_score(title, remix, candidate.get("track", ""))
    cand_artist = candidate.get("artist", "")
    a = max((similarity(artist, cand_artist) for artist in artists), default=0.0)
    # Canonical entries sometimes list all artists in one string ("A, B & C").
    if a < 0.85 and artists and artist_overlap(artists, [cand_artist]) > 0:
        a = max(a, 0.85)
    score = 0.6 * t + 0.4 * a
    if not _remixer_present(remix, {"title": candidate.get("track", ""), "artists": [cand_artist]}):
        score *= 0.5
    return score


def score_youtube_candidate(
    artists: list[str], title: str, remix: str | None, candidate: dict[str, Any]
) -> float:
    """Score a YouTube candidate. Video titles bundle everything into one
    string ('Artist - Title [Official Video]'), so compare whole strings and
    sanity-check the duration."""
    expected = f"{' '.join(artists)} {title}" + (f" {remix}" if remix else "")
    cand_title = candidate.get("title", "")
    base = similarity(expected, cand_title)
    # containment bonus: title present and at least one artist present
    hay = normalize(cand_title + " " + " ".join(candidate.get("artists", [])))
    if normalize(title) in hay and any(normalize(a) in hay for a in artists if a):
        base = max(base, 0.75) + 0.1
    if not _remixer_present(remix, candidate):
        base *= 0.5
    duration_ms = candidate.get("duration_ms")
    if duration_ms is not None and not (60_000 <= duration_ms <= 20 * 60_000):
        base -= 0.3  # not a single track (full set, short, etc.)
    return max(0.0, min(1.0, base))
