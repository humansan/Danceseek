"""Maintainer edits to an already-processed setlist.

The pipeline is LLM-driven end to end, so it is occasionally wrong: an artist
split in the wrong place, a subtitle read as a remix, a Last.fm match that is
the right title by the wrong act. The console's results table is where that
gets fixed, and this module is the rule set behind the save.

Two principles decide everything here:

- `raw_text` is never editable. It is what the source said, and it stays the
  provenance the normalization can always be re-derived from.
- An edit to a track's *identity* (artists / title / remix / ID flag) drops any
  resolution the matcher had stamped on it. The old Last.fm match was chosen
  for the old strings, so keeping it would silently scrobble the wrong track;
  a null slot is honest and the next re-resolve picks it up. A Last.fm target
  the maintainer typed in themselves is the exception — that is the most
  authoritative thing in the record and nothing overwrites it.

The submission is the whole table, not a diff, and reads that way throughout:
a row missing from `tracks` is deleted, and a row whose `lastfm_artist` /
`lastfm_track` come back empty has had its Last.fm identity cleared. The
console always echoes the stored values back, so "unchanged" and "cleared" stay
distinguishable — which is what lets an identity edit invalidate a match while
a hand-typed target survives one.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .models import LastfmMatch, MashupComponent, Resolution, Setlist, SetlistTrack
from .resolver.resolve import build_coverage

# ---------------------------------------------------------------------------
# Request shapes (also the console's JSON body)
# ---------------------------------------------------------------------------


class ComponentEdit(BaseModel):
    """One edited mashup component. Matched to the existing list by order."""

    artists: list[str] = Field(default_factory=list)
    title: str | None = None
    remix: str | None = None
    lastfm_artist: str | None = None
    lastfm_track: str | None = None


class TrackEdit(BaseModel):
    """One edited row. `position` identifies an existing track; null adds one."""

    position: int | None = None
    cue_time: str | None = None
    artists: list[str] = Field(default_factory=list)
    title: str | None = None
    remix: str | None = None
    is_id: bool = False
    lastfm_artist: str | None = None
    lastfm_track: str | None = None
    mashup_components: list[ComponentEdit] = Field(default_factory=list)


class SetlistEdit(BaseModel):
    """The whole table as submitted. Rows absent from `tracks` are deleted."""

    title: str | None = None
    tracks: list[TrackEdit] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Applying an edit
# ---------------------------------------------------------------------------


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def _clean_list(values: list[str]) -> list[str]:
    return [v for v in (s.strip() for s in values) if v]


def _identity(item: SetlistTrack | MashupComponent) -> tuple:
    """The fields a platform match was chosen for."""
    return (
        tuple(getattr(item, "artists", []) or []),
        item.title,
        item.remix,
        bool(getattr(item, "is_id", False)),
    )


def _raw_text(edit: TrackEdit | ComponentEdit) -> str:
    """A display string for a row the maintainer added by hand.

    Real rows carry the source's verbatim text; an invented row has no source,
    so it gets the obvious rendering of what was typed.
    """
    artists = " & ".join(_clean_list(edit.artists))
    title = _clean(edit.title) or ("ID" if getattr(edit, "is_id", False) else "?")
    text = f"{artists or 'ID'} - {title}"
    remix = _clean(edit.remix)
    return f"{text} ({remix})" if remix else text


def _lastfm_target(edit: TrackEdit | ComponentEdit) -> tuple[str, str] | None:
    """The (artist, track) the maintainer wants scrobbled, if they gave both."""
    artist, track = _clean(edit.lastfm_artist), _clean(edit.lastfm_track)
    return (artist, track) if artist and track else None


def _current_lastfm(res: Resolution | None) -> tuple[str, str] | None:
    return (res.lastfm.artist, res.lastfm.track) if res and res.lastfm else None


def _status_for(res: Resolution) -> str:
    """Re-derive a status after a platform field was added or removed."""
    matches = [res.spotify, res.youtube, res.lastfm]
    if not any(matches):
        return "no_match"
    return "resolved" if all(matches) else "partial"


def _apply_resolution(
    item: SetlistTrack | MashupComponent,
    edit: TrackEdit | ComponentEdit,
    identity_changed: bool,
) -> None:
    """Reconcile one item's resolution slot with what was submitted."""
    res = item.resolution
    desired = _lastfm_target(edit)

    # An ID track has nothing to match; that is a terminal state, not a miss.
    if getattr(edit, "is_id", False):
        if not (res and res.status == "unreleased"):
            item.resolution = Resolution(
                status="unreleased", method="skip", notes="marked ID in the console"
            )
        return
    if res is not None and res.status == "unreleased":
        item.resolution = None  # no longer an ID — let the matcher have a go
        res = None

    if desired != _current_lastfm(res):
        if desired is None:
            # Cleared by hand: drop the Last.fm identity, keep any other match.
            if res is not None:
                res.lastfm = None
                res.status = _status_for(res)
                res.method = "manual"
                res.notes = "last.fm match cleared in the console"
            return
        item.resolution = Resolution(
            status="resolved",
            method="manual",
            confidence=1.0,
            track_id=res.track_id if res else None,
            spotify=res.spotify if res else None,
            youtube=res.youtube if res else None,
            lastfm=LastfmMatch(artist=desired[0], track=desired[1]),
            notes="last.fm target set by hand in the console",
        )
        return

    # Same Last.fm target as before. Only a changed identity invalidates it —
    # and never a target that was set by hand in the first place.
    if identity_changed and res is not None and res.method != "manual":
        item.resolution = None


def _apply_component(existing: MashupComponent | None, edit: ComponentEdit) -> MashupComponent:
    component = existing or MashupComponent()
    before = _identity(component)
    component.artists = _clean_list(edit.artists)
    component.title = _clean(edit.title)
    component.remix = _clean(edit.remix)
    _apply_resolution(component, edit, identity_changed=_identity(component) != before)
    return component


def _apply_track(existing: SetlistTrack | None, edit: TrackEdit) -> SetlistTrack:
    track = existing or SetlistTrack(position=0, raw_text=_raw_text(edit))
    before = _identity(track)

    track.cue_time = _clean(edit.cue_time)
    track.artists = _clean_list(edit.artists)
    track.title = _clean(edit.title)
    track.remix = _clean(edit.remix)
    track.is_id = edit.is_id

    old_components = list(track.mashup_components)
    track.mashup_components = [
        _apply_component(old_components[i] if i < len(old_components) else None, c)
        for i, c in enumerate(edit.mashup_components)
    ]
    _apply_resolution(track, edit, identity_changed=_identity(track) != before)
    return track


def _renumber(tracks: list[SetlistTrack], old_positions: list[int | None]) -> None:
    """Make positions dense and in table order, keeping `played_with` pointing
    at the same row (or nowhere, if that row was deleted)."""
    remap = {
        old: index
        for old, index in zip(old_positions, range(1, len(tracks) + 1))
        if old is not None
    }
    for index, track in enumerate(tracks, start=1):
        track.position = index
    for track in tracks:
        if track.played_with is not None:
            track.played_with = remap.get(track.played_with)


def apply_edits(setlist: Setlist, edit: SetlistEdit) -> Setlist:
    """Fold a submitted table back into `setlist`, in place. Does NOT persist."""
    if not edit.tracks:
        raise ValueError("An edit must keep at least one track.")

    by_position = {t.position: t for t in setlist.tracks}
    seen: set[int] = set()
    tracks: list[SetlistTrack] = []
    old_positions: list[int | None] = []

    for row in edit.tracks:
        existing = by_position.get(row.position) if row.position is not None else None
        if existing is not None:
            if row.position in seen:
                raise ValueError(f"Position {row.position} submitted twice.")
            seen.add(row.position)
        tracks.append(_apply_track(existing, row))
        old_positions.append(row.position if existing is not None else None)

    _renumber(tracks, old_positions)
    setlist.tracks = tracks
    if edit.title is not None:
        setlist.title = _clean(edit.title)
    return setlist


# ---------------------------------------------------------------------------
# Coverage after an edit
# ---------------------------------------------------------------------------


class _Counts:
    """Enough of a ResolveSummary for `build_coverage`, counted from the record.

    Slots the edit invalidated are null, so they fall out of every bucket —
    which is the point: `total` dropping below the track count is the signal
    that a re-resolve is owed.
    """

    def __init__(self) -> None:
        self.resolved = self.partial = self.no_match = self.unreleased = 0
        self.skipped = self.registry_hits = 0
        self.platforms: list[str] = []


def coverage_for(setlist: Setlist, platforms: list[str] | None = None) -> dict[str, Any]:
    """Recompute the coverage summary from the stored resolutions."""
    counts = _Counts()
    counts.platforms = list(platforms or [])
    for track in setlist.tracks:
        for item in (track, *track.mashup_components):
            if item.resolution is not None:
                setattr(counts, item.resolution.status, getattr(counts, item.resolution.status) + 1)
    coverage = build_coverage(setlist, counts)  # type: ignore[arg-type]
    coverage["pending"] = sum(
        1
        for track in setlist.tracks
        for item in (track, *track.mashup_components)
        if item.resolution is None
    )
    return coverage


def save_edited(setlist: Setlist, meta: dict[str, Any]) -> dict[str, Any]:
    """Persist an edited setlist and its recomputed coverage. Returns coverage.

    The lifecycle `status` is carried through untouched: an edit is not a
    pipeline stage, and flipping a set out of "resolved" would lock exports
    over something as small as a corrected cue time. What an edit invalidated
    shows up as `coverage["pending"]` instead.
    """
    from . import db, store

    store.save(setlist)
    db.upsert_content(setlist)
    previous = meta.get("coverage") or {}
    coverage = coverage_for(setlist, previous.get("platforms"))
    db.set_coverage(setlist.id, coverage, status=meta.get("status") or "resolved")
    return coverage
