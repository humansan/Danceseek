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


# ---------------------------------------------------------------------------
# Production repository (status/coverage aware) — used by the Postgres store
# backend and the API/worker. Content upserts never clobber lifecycle columns.
# ---------------------------------------------------------------------------

_META_COLUMNS = "status, coverage, resolved_at, created_at"

CONTENT_UPSERT = """
INSERT INTO setlists (id, source_url, source, title, dj_names, event, date_recorded,
                      genres, media_url, media_kind, scraped_at, parser, tracks, status, pushed_at)
VALUES (%(id)s, %(source_url)s, %(source)s, %(title)s, %(dj_names)s, %(event)s,
        %(date_recorded)s, %(genres)s, %(media_url)s, %(media_kind)s, %(scraped_at)s,
        %(parser)s, %(tracks)s, COALESCE(%(status)s, 'resolving'), now())
ON CONFLICT (source_url) DO UPDATE SET
    id = EXCLUDED.id, source = EXCLUDED.source, title = EXCLUDED.title,
    dj_names = EXCLUDED.dj_names, event = EXCLUDED.event,
    date_recorded = EXCLUDED.date_recorded, genres = EXCLUDED.genres,
    media_url = EXCLUDED.media_url, media_kind = EXCLUDED.media_kind,
    scraped_at = EXCLUDED.scraped_at, parser = EXCLUDED.parser, tracks = EXCLUDED.tracks,
    status = COALESCE(%(status)s, setlists.status), pushed_at = now()
"""


def upsert_content(setlist: Setlist, status: str | None = None) -> None:
    """Upsert setlist content, preserving coverage/created_at and (unless given) status."""
    row = to_row(setlist)
    row["status"] = status
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(CONTENT_UPSERT, row)
        conn.commit()
    finally:
        conn.close()


def set_status(setlist_id: str, status: str) -> None:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE setlists SET status = %s WHERE id = %s", (status, setlist_id))
        conn.commit()
    finally:
        conn.close()


def set_coverage(setlist_id: str, coverage: dict, status: str = "resolved") -> None:
    from psycopg.types.json import Jsonb

    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE setlists SET coverage = %s, status = %s, resolved_at = now() WHERE id = %s",
                (Jsonb(coverage), status, setlist_id),
            )
        conn.commit()
    finally:
        conn.close()


def _split_row(row: tuple) -> tuple[Setlist, dict[str, Any]]:
    n = len([k for k in _COLUMNS.split(",")])
    setlist = from_row(row[:n])
    status, coverage, resolved_at, created_at = row[n:]
    if isinstance(coverage, str):
        coverage = json.loads(coverage)
    meta = {
        "status": status,
        "coverage": coverage,
        "resolved_at": str(resolved_at) if resolved_at else None,
        "created_at": str(created_at) if created_at else None,
    }
    return setlist, meta


def get_by_id(setlist_id: str) -> tuple[Setlist, dict[str, Any]] | None:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {_COLUMNS}, {_META_COLUMNS} FROM setlists WHERE id = %s", (setlist_id,))
            row = cur.fetchone()
        return _split_row(row) if row else None
    finally:
        conn.close()


def get_by_url(source_url: str) -> tuple[Setlist, dict[str, Any]] | None:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {_COLUMNS}, {_META_COLUMNS} FROM setlists WHERE source_url = %s", (source_url,))
            row = cur.fetchone()
        return _split_row(row) if row else None
    finally:
        conn.close()


def all_setlists() -> list[Setlist]:
    """Every stored setlist as a full Setlist, newest first."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {_COLUMNS} FROM setlists ORDER BY created_at DESC")
            rows = cur.fetchall()
        return [from_row(r) for r in rows]
    finally:
        conn.close()


def lookup_id(source_url: str) -> str | None:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM setlists WHERE source_url = %s", (source_url,))
            row = cur.fetchone()
        return str(row[0]) if row else None
    finally:
        conn.close()


def list_summaries(
    limit: int = 50, offset: int = 0, q: str | None = None,
    dj: str | None = None, genre: str | None = None,
) -> list[dict[str, Any]]:
    """Card/browse rows: lightweight, newest first, with optional filters."""
    where, params = [], []
    if q:
        where.append("(title ILIKE %s OR event ILIKE %s)")
        params += [f"%{q}%", f"%{q}%"]
    if dj:
        where.append("%s = ANY(dj_names)")
        params.append(dj)
    if genre:
        where.append("%s = ANY(genres)")
        params.append(genre)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    params += [limit, offset]
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT id, title, dj_names, event, date_recorded, genres, media_url,
                           jsonb_array_length(tracks), status, coverage, created_at
                    FROM setlists {clause}
                    ORDER BY created_at DESC LIMIT %s OFFSET %s""",
                params,
            )
            rows = cur.fetchall()
        out = []
        for r in rows:
            cov = json.loads(r[9]) if isinstance(r[9], str) else r[9]
            out.append({
                "id": str(r[0]), "title": r[1], "dj_names": r[2], "event": r[3],
                "date_recorded": r[4], "genres": r[5], "media_url": r[6],
                "track_count": r[7], "status": r[8], "coverage": cov,
                "created_at": str(r[10]) if r[10] else None,
            })
        return out
    finally:
        conn.close()


def clear() -> int:
    """Delete every row from the cache table. Returns the number removed."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM setlists")
            removed = cur.rowcount
        conn.commit()
        return removed
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
