"""Export orchestrator: collect -> auth -> create playlist -> add -> report."""

from __future__ import annotations

from dataclasses import dataclass

from ..models import Setlist
from .collect import ExportPlan, Target, build_plan


@dataclass
class ExportResult:
    plan: ExportPlan
    url: str | None = None  # None on a dry run
    added: int = 0
    dry_run: bool = False


def _default_name(setlist: Setlist) -> str:
    return f"{setlist.title or 'Setlist'} (via SoundSeek)"


def _description(setlist: Setlist) -> str:
    return f"Imported from 1001tracklists by SoundSeek. Source: {setlist.source_url}"


def export_setlist(
    setlist: Setlist,
    target: Target,
    name: str | None = None,
    public: bool = False,
    expand_mashups: bool = True,
    skip_played_with: bool = False,
    dry_run: bool = False,
) -> ExportResult:
    plan = build_plan(setlist, target, expand_mashups, skip_played_with)

    if dry_run or not plan.items:
        return ExportResult(plan=plan, dry_run=dry_run)

    playlist_name = name or _default_name(setlist)
    description = _description(setlist)

    if target == "spotify":
        from .spotify import SpotifyExporter

        result = SpotifyExporter().export(plan, playlist_name, description, public)
    else:
        from .youtube import YouTubeExporter

        result = YouTubeExporter().export(plan, playlist_name, description, public)

    return ExportResult(plan=plan, url=result["url"], added=result["added"])
