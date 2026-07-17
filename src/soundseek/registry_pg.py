"""Postgres-backed track registry: the `tracks` table stands in for the local
data/tracks.json file so a server-side worker can share the cross-set cache.

Same interface and dedupe priority as registry.Registry — only the load and
persist seams change: load all rows once at construction, then write through a
single record per find_or_create (instead of rewriting the whole file).
"""

from __future__ import annotations

from . import db
from .models import TrackRecord
from .registry import Registry


class PgRegistry(Registry):
    def _load_all(self) -> dict[str, TrackRecord]:
        return {rec.id: rec for rec in db.all_tracks()}

    def _persist(self, rec: TrackRecord) -> None:
        db.upsert_track(rec)
