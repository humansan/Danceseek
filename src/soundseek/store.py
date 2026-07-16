"""JSON persistence for setlists.

Layout (shaped to migrate 1:1 to the future Neon/Postgres schema):
    data/setlists/<id>.json   one Setlist record each
    data/index.json           source_url -> setlist id (dedupe on re-ingest)
"""

from __future__ import annotations

import json

from .config import settings
from .models import Setlist


def _load_index() -> dict[str, str]:
    if settings.index_path.exists():
        return json.loads(settings.index_path.read_text(encoding="utf-8"))
    return {}


def _write_index(index: dict[str, str]) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.index_path.write_text(
        json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def lookup(source_url: str) -> str | None:
    """Return the stored setlist id for a URL, if already ingested."""
    return _load_index().get(source_url)


def setlist_path(setlist_id: str):
    return settings.setlists_dir / f"{setlist_id}.json"


def save(setlist: Setlist) -> None:
    """Persist a setlist and index it by source URL (replaces any previous record)."""
    index = _load_index()
    old_id = index.get(setlist.source_url)
    if old_id and old_id != setlist.id:
        setlist_path(old_id).unlink(missing_ok=True)

    settings.setlists_dir.mkdir(parents=True, exist_ok=True)
    setlist_path(setlist.id).write_text(
        setlist.model_dump_json(indent=2), encoding="utf-8"
    )
    index[setlist.source_url] = setlist.id
    _write_index(index)


def load(setlist_id: str) -> Setlist:
    return Setlist.model_validate_json(
        setlist_path(setlist_id).read_text(encoding="utf-8")
    )


def load_by_url(source_url: str) -> Setlist | None:
    setlist_id = lookup(source_url)
    return load(setlist_id) if setlist_id else None


def list_all() -> list[Setlist]:
    """All stored setlists, newest first."""
    setlists = [
        Setlist.model_validate_json(p.read_text(encoding="utf-8"))
        for p in sorted(settings.setlists_dir.glob("*.json"))
    ]
    return sorted(setlists, key=lambda s: s.scraped_at, reverse=True)
