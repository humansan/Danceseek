"""Cue windows: which track is playing at a given point in the set recording.

One track's window runs from its cue time to the next cue. This is the single
source of truth for two things that must never disagree:

  * the UI highlight — the row under the playhead, and the now-playing readout
  * the scrobbler — what gets submitted, and under which name

Which is why it lives here (pure, no I/O) rather than in the browser: the
client reports a playhead, the server decides what that means.

Sets with no cue times at all still produce windows, evenly spread and marked
`timing="estimated"`. Those can't drive live scrobbling — without cues there's
no way to know what's playing — but they can be scrobbled in one action as a
whole set, which is the point of that mode.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from ..models import Setlist, SetlistTrack

# Last.fm won't accept a track shorter than 30s, so a window below that can
# never be scrobbled however long the playhead sits in it.
MIN_WINDOW_S = 30
# The last cue has no successor; assume a closing track of this length when the
# recording's duration is unknown.
DEFAULT_TAIL_S = 480
# Per-track guess when a set has neither cues nor a known duration.
DEFAULT_TRACK_S = 300

Timing = Literal["cue", "estimated"]


class CueWindow(BaseModel):
    """One track's slice of the recording, plus how to name it."""

    position: int
    # Set on a mashup's component tracks, which share their parent's window.
    # The parent row itself is never scrobbled — "A vs. B" is not a track.
    component_index: int | None = None
    start_s: int
    end_s: int
    timing: Timing
    label: str  # display text: "Artist – Title"
    # The canonical Last.fm identity when we have one, else our normalized
    # names — never the raw 1001tracklists string.
    scrobble_artist: str | None = None
    scrobble_track: str | None = None
    canonical: bool = False  # True when the names came from a Last.fm match
    eligible: bool = True
    reason: str | None = None  # why not, when eligible is False

    @property
    def duration_s(self) -> int:
        return self.end_s - self.start_s


class WindowSet(BaseModel):
    """Every window for a setlist, plus what the caller may do with them."""

    setlist_id: str
    timing: Timing
    # Live highlighting/scrobbling needs real cue times; estimated sets get
    # whole-set scrobbling only.
    live_capable: bool
    duration_s: int
    windows: list[CueWindow]


def cue_seconds(text: str | None) -> int | None:
    """'1:02:30' -> 3750, '5:27' -> 327. None for missing or unparseable."""
    if not text:
        return None
    parts = text.strip().split(":")
    if not 2 <= len(parts) <= 3:
        return None
    try:
        values = [int(p) for p in parts]
    except ValueError:
        return None
    if any(v < 0 for v in values):
        return None
    if len(values) == 2:
        minutes, seconds = values
        return minutes * 60 + seconds
    hours, minutes, seconds = values
    return hours * 3600 + minutes * 60 + seconds


def _names(item) -> tuple[str | None, str | None, bool]:
    """(artist, track, canonical) for a track or a mashup component.

    A Last.fm match wins outright — those exact strings are the scrobble
    identity. Otherwise we build a name from our normalized fields:

      * **Primary artist only.** Joining every credit ("Knock2, Sophia
        Gripari") would scrobble a single literal artist of that name, which
        links to nobody's page and litters the library.
      * **The version is kept** ("my melody (VIP)"), so an edit isn't logged as
        if it were the original.
    """
    resolution = getattr(item, "resolution", None)
    if resolution and resolution.lastfm:
        return resolution.lastfm.artist, resolution.lastfm.track, True

    artists = getattr(item, "artists", None) or []
    artist = artists[0] if artists else None
    title = getattr(item, "title", None)
    remix = getattr(item, "remix", None)
    if title and remix:
        title = f"{title} ({remix})"
    return artist, title, False


def _build(
    item,
    *,
    position: int,
    start_s: int,
    end_s: int,
    timing: Timing,
    component_index: int | None = None,
    fallback_label: str = "",
    force_reason: str | None = None,
) -> CueWindow:
    artist, name, canonical = _names(item)
    is_id = bool(getattr(item, "is_id", False))

    if is_id:
        label = "ID · unreleased"
    elif artist and name:
        label = f"{artist} – {name}"
    else:
        label = name or artist or fallback_label

    eligible, reason = True, None
    if force_reason:
        eligible, reason = False, force_reason
    elif is_id:
        eligible, reason = False, "unreleased"
    elif not name:
        eligible, reason = False, "no track name"
    elif end_s - start_s < MIN_WINDOW_S:
        eligible, reason = False, f"window under {MIN_WINDOW_S}s"

    return CueWindow(
        position=position,
        component_index=component_index,
        start_s=start_s,
        end_s=end_s,
        timing=timing,
        label=label,
        scrobble_artist=artist,
        scrobble_track=name,
        canonical=canonical,
        eligible=eligible,
        reason=reason,
    )


def _windows_for(track: SetlistTrack, start_s: int, end_s: int, timing: Timing) -> list[CueWindow]:
    """A track's window — plus one per mashup component.

    A mashup's parent row is a blob ("A vs. B vs. C"), not a track anyone can
    scrobble, so it is never eligible. Its components each resolve on their own
    and share the parent's slice of the recording.
    """
    components = getattr(track, "mashup_components", None) or []
    parent = _build(
        track,
        position=track.position,
        start_s=start_s,
        end_s=end_s,
        timing=timing,
        fallback_label=track.raw_text,
        force_reason="mashup — components scrobble separately" if components else None,
    )
    if not components:
        return [parent]

    return [parent] + [
        _build(
            component,
            position=track.position,
            component_index=index,
            start_s=start_s,
            end_s=end_s,
            timing=timing,
        )
        for index, component in enumerate(components)
    ]


def _from_cues(
    tracks: list[SetlistTrack], timed: list[tuple[int, int]], duration_s: int | None
) -> list[CueWindow]:
    """Windows from real cue times; untimed rows inherit the window they sit in.

    A `w/` row usually carries no cue of its own — it's layered over the row
    above — so it shares that row's window rather than vanishing from the set.
    """
    last_start = timed[-1][1]
    tail = duration_s if duration_s and duration_s > last_start else last_start + DEFAULT_TAIL_S

    bounds: dict[int, tuple[int, int]] = {}
    for n, (index, start) in enumerate(timed):
        end = timed[n + 1][1] if n + 1 < len(timed) else tail
        bounds[index] = (start, max(end, start))

    windows: list[CueWindow] = []
    current = bounds[timed[0][0]]  # rows before the first cue join the first window
    for index, track in enumerate(tracks):
        if index in bounds:
            current = bounds[index]
        windows.extend(_windows_for(track, current[0], current[1], "cue"))
    return windows


def _estimated(tracks: list[SetlistTrack], duration_s: int | None) -> list[CueWindow]:
    """No cues anywhere: spread the tracks evenly across the recording."""
    count = len(tracks)
    total = duration_s if duration_s and duration_s > 0 else count * DEFAULT_TRACK_S
    step = total / count
    windows: list[CueWindow] = []
    for i, track in enumerate(tracks):
        windows.extend(_windows_for(track, round(i * step), round((i + 1) * step), "estimated"))
    return windows


def build_windows(setlist: Setlist, media_duration_s: int | None = None) -> WindowSet:
    """Cue windows for a setlist, in playing order."""
    tracks = sorted(setlist.tracks, key=lambda t: t.position)
    if not tracks:
        return WindowSet(
            setlist_id=setlist.id, timing="estimated", live_capable=False,
            duration_s=media_duration_s or 0, windows=[],
        )

    timed = [
        (index, seconds)
        for index, track in enumerate(tracks)
        if (seconds := cue_seconds(track.cue_time)) is not None
    ]
    # Cues must advance; a single stray timestamp out of order would otherwise
    # produce a negative-length window.
    timed = [pair for n, pair in enumerate(timed) if n == 0 or pair[1] >= timed[n - 1][1]]

    if timed:
        windows = _from_cues(tracks, timed, media_duration_s)
        timing: Timing = "cue"
    else:
        windows = _estimated(tracks, media_duration_s)
        timing = "estimated"

    return WindowSet(
        setlist_id=setlist.id,
        timing=timing,
        live_capable=timing == "cue",
        duration_s=media_duration_s or (windows[-1].end_s if windows else 0),
        windows=windows,
    )
