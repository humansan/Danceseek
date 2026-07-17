"""Postgres (Neon) persistence backend, matching the store interface used by
the pipeline and resolver. Content writes preserve lifecycle columns
(status/coverage) — the API/worker set those via db.set_status/set_coverage.
"""

from __future__ import annotations

from . import db
from .models import Setlist


def lookup(source_url: str) -> str | None:
    return db.lookup_id(source_url)


def setlist_path(setlist_id: str) -> str:
    return f"(neon:setlists/{setlist_id})"


def save(setlist: Setlist) -> None:
    db.upsert_content(setlist)


def load(setlist_id: str) -> Setlist:
    result = db.get_by_id(setlist_id)
    if result is None:
        raise FileNotFoundError(setlist_id)
    return result[0]


def load_by_url(source_url: str) -> Setlist | None:
    result = db.get_by_url(source_url)
    return result[0] if result else None


def list_all() -> list[Setlist]:
    return db.all_setlists()
