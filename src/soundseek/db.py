"""Minimal Neon Postgres layer: a single cache table of processed setlists.

Purpose: serve already-processed lists so a request never triggers
reprocessing. Metadata lives in columns, the whole tracks array (with
resolutions) is one JSONB blob. Deliberately no ORM, no relations, no
migrations — the future relational schema is a separate step.
"""

from __future__ import annotations

import json
import os
from typing import Any

from .models import Setlist

DDL = """
CREATE TABLE IF NOT EXISTS setlists (
    id             UUID PRIMARY KEY,
    source_url     TEXT NOT NULL UNIQUE,
    source         TEXT NOT NULL,
    title          TEXT,
    dj_names       TEXT[] NOT NULL DEFAULT '{}',
    event          TEXT,
    date_recorded  TEXT,
    genres         TEXT[] NOT NULL DEFAULT '{}',
    media_url      TEXT,
    media_kind     TEXT,
    scraped_at     TEXT NOT NULL,
    parser         JSONB NOT NULL,
    tracks         JSONB NOT NULL,
    pushed_at      TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

UPSERT = """
INSERT INTO setlists (id, source_url, source, title, dj_names, event, date_recorded,
                      genres, media_url, media_kind, scraped_at, parser, tracks, pushed_at)
VALUES (%(id)s, %(source_url)s, %(source)s, %(title)s, %(dj_names)s, %(event)s,
        %(date_recorded)s, %(genres)s, %(media_url)s, %(media_kind)s, %(scraped_at)s,
        %(parser)s, %(tracks)s, now())
ON CONFLICT (source_url) DO UPDATE SET
    id = EXCLUDED.id, source = EXCLUDED.source, title = EXCLUDED.title,
    dj_names = EXCLUDED.dj_names, event = EXCLUDED.event,
    date_recorded = EXCLUDED.date_recorded, genres = EXCLUDED.genres,
    media_url = EXCLUDED.media_url, media_kind = EXCLUDED.media_kind,
    scraped_at = EXCLUDED.scraped_at, parser = EXCLUDED.parser,
    tracks = EXCLUDED.tracks, pushed_at = now()
"""

_COLUMNS = (
    "id, source_url, source, title, dj_names, event, date_recorded, "
    "genres, media_url, media_kind, scraped_at, parser, tracks"
)


class DbError(RuntimeError):
    """Missing configuration or database failure."""


def to_row(setlist: Setlist) -> dict[str, Any]:
    """Pure: Setlist -> upsert parameters (JSONB fields as wrapped JSON)."""
    from psycopg.types.json import Jsonb

    dump = setlist.model_dump(mode="json")
    return {
        "id": dump["id"],
        "source_url": dump["source_url"],
        "source": dump["source"],
        "title": dump["title"],
        "dj_names": dump["dj_names"],
        "event": dump["event"],
        "date_recorded": dump["date_recorded"],
        "genres": dump["genres"],
        "media_url": dump["media_url"],
        "media_kind": dump["media_kind"],
        "scraped_at": dump["scraped_at"],
        "parser": Jsonb(dump["parser"]),
        "tracks": Jsonb(dump["tracks"]),
    }


def from_row(row: tuple) -> Setlist:
    """Pure: SELECT row (in _COLUMNS order) -> Setlist."""
    keys = [k.strip() for k in _COLUMNS.split(",")]
    data = dict(zip(keys, row))
    data["id"] = str(data["id"])  # UUID -> str
    # psycopg may return JSONB as already-parsed objects or as strings
    for field in ("parser", "tracks"):
        if isinstance(data[field], str):
            data[field] = json.loads(data[field])
    return Setlist.model_validate(data)


def _connect():
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise DbError("DATABASE_URL not set in .env — add your Neon connection string.")
    import psycopg  # lazy: only needed when the db is actually used

    try:
        conn = psycopg.connect(url)
    except Exception as e:
        raise DbError(f"Could not connect to database: {e}") from e
    # NOTE: `with conn:` would CLOSE the connection on exit (psycopg3 semantics),
    # so run the idempotent DDL with an explicit commit instead.
    with conn.cursor() as cur:
        cur.execute(DDL)
    conn.commit()
    return conn


def push(setlist: Setlist) -> None:
    """Upsert one setlist (keyed by source_url)."""
    conn = _connect()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(UPSERT, to_row(setlist))
    finally:
        conn.close()


def fetch(source_url: str) -> Setlist | None:
    """Load a processed setlist from the cache table, or None."""
    conn = _connect()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(f"SELECT {_COLUMNS} FROM setlists WHERE source_url = %s", (source_url,))
            row = cur.fetchone()
        return from_row(row) if row else None
    finally:
        conn.close()


def list_remote() -> list[dict[str, Any]]:
    """Summary of everything in the cache table, newest push first."""
    conn = _connect()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, title, jsonb_array_length(tracks), pushed_at "
                "FROM setlists ORDER BY pushed_at DESC"
            )
            rows = cur.fetchall()
        return [
            {"id": str(r[0]), "title": r[1], "tracks": r[2], "pushed_at": str(r[3])}
            for r in rows
        ]
    finally:
        conn.close()
