"""Submitting plays to Last.fm.

The server signs and sends everything: the client only ever reports a playhead
and an intent. That's deliberate — the session key is a permanent write
credential for the user's account and must never reach a browser.

Two calls matter:
  * track.updateNowPlaying — the "listening now" flag, not a play
  * track.scrobble         — the play itself, up to 50 per request
"""

from __future__ import annotations

from dataclasses import dataclass

from .auth import LastfmAuthError, signed_call

# Last.fm's documented cap for a single track.scrobble request.
BATCH_SIZE = 50


@dataclass
class Play:
    """One play: canonical names plus when it started (unix seconds).

    `album` is the DJ set the track was heard in, and `album_artist` the DJ —
    together they group a set's plays under one album instead of scattering
    them as loose singles.
    """

    artist: str
    track: str
    timestamp: int
    album: str | None = None
    album_artist: str | None = None


@dataclass
class SubmitResult:
    accepted: int = 0
    ignored: int = 0
    # (index, message) for anything Last.fm declined, e.g. a timestamp too old.
    problems: list[tuple[int, str]] | None = None

    def __post_init__(self) -> None:
        if self.problems is None:
            self.problems = []


def update_now_playing(
    session_key: str,
    artist: str,
    track: str,
    duration_s: int | None = None,
    album: str | None = None,
    album_artist: str | None = None,
) -> None:
    """Flag what's playing. Best-effort: this is a status, not a play."""
    params = {"artist": artist, "track": track, "sk": session_key}
    if duration_s and duration_s > 0:
        params["duration"] = str(int(duration_s))
    if album:
        params["album"] = album
    if album_artist:
        params["albumArtist"] = album_artist
    signed_call("track.updateNowPlaying", **params)


def _batch_params(plays: list[Play]) -> dict[str, str]:
    params: dict[str, str] = {}
    for i, play in enumerate(plays):
        params[f"artist[{i}]"] = play.artist
        params[f"track[{i}]"] = play.track
        params[f"timestamp[{i}]"] = str(int(play.timestamp))
        if play.album:
            params[f"album[{i}]"] = play.album
        if play.album_artist:
            params[f"albumArtist[{i}]"] = play.album_artist
    return params


def _read_result(body: dict, offset: int) -> SubmitResult:
    block = body.get("scrobbles") or {}
    attr = block.get("@attr") or {}
    result = SubmitResult(
        accepted=int(attr.get("accepted") or 0), ignored=int(attr.get("ignored") or 0)
    )

    entries = block.get("scrobble") or []
    if isinstance(entries, dict):  # a single scrobble comes back unwrapped
        entries = [entries]
    for i, entry in enumerate(entries):
        ignored = (entry or {}).get("ignoredMessage") or {}
        text = ignored.get("#text") or ""
        if ignored.get("code") not in (None, "0", 0) and text:
            result.problems.append((offset + i, text))
    return result


def scrobble(session_key: str, plays: list[Play]) -> SubmitResult:
    """Submit plays, chunked to Last.fm's 50-per-request limit."""
    if not plays:
        return SubmitResult()

    total = SubmitResult()
    for start in range(0, len(plays), BATCH_SIZE):
        chunk = plays[start : start + BATCH_SIZE]
        body = signed_call("track.scrobble", sk=session_key, **_batch_params(chunk))
        part = _read_result(body, start)
        total.accepted += part.accepted
        total.ignored += part.ignored
        total.problems.extend(part.problems)
    return total


__all__ = ["Play", "SubmitResult", "LastfmAuthError", "scrobble", "update_now_playing", "BATCH_SIZE"]
