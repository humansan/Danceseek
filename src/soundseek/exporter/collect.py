"""Turn a resolved Setlist into an ordered list of platform IDs to add.

Pure and network-free — the whole export policy lives here so it can be unit
tested and previewed with `--dry-run`. Only tracks with a stored,
threshold-cleared match are included (precision over recall); everything else
is reported as skipped with a reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from ..models import MashupComponent, Resolution, Setlist, SetlistTrack

Target = Literal["spotify", "youtube"]
SkipReason = Literal["unreleased", "no_match", "played_with_skipped", "duplicate"]


@dataclass
class PlanItem:
    id: str
    label: str  # human-readable, for the coverage report


@dataclass
class ExportPlan:
    target: Target
    items: list[PlanItem] = field(default_factory=list)
    skipped: list[tuple[str, SkipReason]] = field(default_factory=list)

    @property
    def added(self) -> int:
        return len(self.items)

    @property
    def total_considered(self) -> int:
        return self.added + len(self.skipped)


def _match_id(resolution: Resolution | None, target: Target) -> str | None:
    if resolution is None:
        return None
    match = getattr(resolution, target)
    return match.id if match else None


def _component_label(component: MashupComponent) -> str:
    who = " & ".join(component.artists) if component.artists else "?"
    return f"{who} - {component.title or '?'}"


def build_plan(
    setlist: Setlist,
    target: Target,
    expand_mashups: bool = True,
    skip_played_with: bool = False,
) -> ExportPlan:
    """Build the ordered add-list + coverage report for one target platform."""
    plan = ExportPlan(target=target)
    seen: set[str] = set()

    def add(item_id: str | None, label: str, empty_reason: SkipReason) -> None:
        if not item_id:
            plan.skipped.append((label, empty_reason))
            return
        if item_id in seen:
            plan.skipped.append((label, "duplicate"))
            return
        seen.add(item_id)
        plan.items.append(PlanItem(id=item_id, label=label))

    for track in setlist.tracks:
        if track.is_id:
            plan.skipped.append((track.raw_text, "unreleased"))
            continue

        if track.mashup_components:
            _add_mashup(track, target, expand_mashups, add, plan)
            continue

        if skip_played_with and track.played_with is not None:
            plan.skipped.append((track.raw_text, "played_with_skipped"))
            continue

        add(_match_id(track.resolution, target), track.raw_text, "no_match")

    return plan


def _add_mashup(
    track: SetlistTrack,
    target: Target,
    expand_mashups: bool,
    add,
    plan: ExportPlan,
) -> None:
    # YouTube: the whole mashup often exists as one bootleg upload — prefer it.
    if target == "youtube":
        row_id = _match_id(track.resolution, "youtube")
        if row_id:
            add(row_id, track.raw_text, "no_match")
            return
    # Otherwise fall back to the resolved components (Spotify only has these).
    if not expand_mashups:
        plan.skipped.append((track.raw_text, "no_match"))
        return
    for component in track.mashup_components:
        add(_match_id(component.resolution, target), _component_label(component), "no_match")
