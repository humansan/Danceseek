"""Minimal Neon Postgres layer: a single cache table of processed setlists.

Purpose: serve already-processed lists so a request never triggers
reprocessing. Metadata lives in columns, the whole tracks array (with
resolutions) is one JSONB blob. Deliberately no ORM, no relations, no
migrations — the future relational schema is a separate step.
"""

from __future__ import annotations

import json
import os
import threading
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


# The bootstrap DDL only has to run once per process — re-running it on every
# connection cost a round trip per request. Schema changes go through Alembic;
# this is just the "works on a fresh database" safety net.
_DDL_APPLIED = False

# Opening a connection to Neon costs ~550ms from here (TLS + auth) against a
# ~130ms query — roughly 80% of every call was handshake. The pool keeps
# connections warm so that cost is paid once per worker, not once per request.
_pool = None
_pool_lock = threading.Lock()


class _Pooled:
    """A pooled connection that behaves like a plain one.

    Every call site in this module ends with `conn.close()`; here that returns
    the connection to the pool instead of dropping it, so the existing code
    keeps working unchanged. `with conn:` still commits (or rolls back) exactly
    as psycopg3 does, then releases.
    """

    __slots__ = ("_conn", "_pool", "_released")

    def __init__(self, pool, conn) -> None:
        self._pool, self._conn, self._released = pool, conn, False

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def close(self) -> None:
        if not self._released:
            self._released = True
            self._pool.putconn(self._conn)  # rolls back any open transaction

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            self._conn.commit()
        else:
            self._conn.rollback()
        self.close()
        return False


def _get_pool():
    global _pool
    if _pool is not None:
        return _pool
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise DbError("DATABASE_URL not set in .env — add your Neon connection string.")
    with _pool_lock:
        if _pool is None:
            from psycopg_pool import ConnectionPool

            try:
                # autocommit: every statement here is self-contained, so an
                # implicit transaction would only mean the pool rolling one back
                # on every release — an extra round trip per request. Anything
                # that needs atomicity opens `with conn.transaction():` itself
                # (see claim_job).
                #
                # The rest of this is all about one thing: Neon hangs up on idle
                # connections (the compute autosuspends), and the pool has no way
                # to know. A pooled connection that has been sitting since the
                # last request is very likely already dead, and handing it out
                # raises "SSL connection has been closed unexpectedly" on the
                # first query — which looked exactly like the API cold-starting
                # after a quiet spell. So:
                #   check        — ping before handing a connection out; a dead
                #                  one is discarded and replaced transparently,
                #                  which is what actually fixes the bug.
                #   max_lifetime — retire connections well inside Neon's idle
                #                  window instead of nursing stale ones.
                #   max_idle     — let the pool shrink back to min_size.
                #   keepalives   — keep the TCP session alive through Neon's
                #                  proxy so it is less likely to die at all.
                _pool = ConnectionPool(
                    url,
                    min_size=1,
                    max_size=10,
                    timeout=15,
                    open=True,
                    check=ConnectionPool.check_connection,
                    max_lifetime=300,
                    max_idle=120,
                    kwargs={
                        "autocommit": True,
                        "keepalives": 1,
                        "keepalives_idle": 30,
                        "keepalives_interval": 10,
                        "keepalives_count": 3,
                    },
                )
            except Exception as e:
                raise DbError(f"Could not connect to database: {e}") from e
    return _pool


def close_pool() -> None:
    """Release pooled connections — call on service shutdown."""
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def _connect():
    global _DDL_APPLIED

    pool = _get_pool()
    try:
        conn = _Pooled(pool, pool.getconn())
    except Exception as e:
        raise DbError(f"Could not connect to database: {e}") from e

    if not _DDL_APPLIED:
        with conn.cursor() as cur:
            cur.execute(DDL)
        conn.commit()
        _DDL_APPLIED = True  # a race here just repeats harmless idempotent DDL
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


# The set's length, taken as the last cue in the tracklist. The final track is
# still playing after its cue, so this reads slightly short — close enough for a
# browse card, and it needs no extra column (Armin: last cue 1:16:33 vs a real
# 1:17:39).
_LAST_CUE_SQL = """
    (SELECT elem->>'cue_time'
       FROM jsonb_array_elements(tracks) WITH ORDINALITY AS a(elem, ord)
      WHERE elem->>'cue_time' IS NOT NULL
      ORDER BY a.ord DESC LIMIT 1)
"""


def list_summaries(
    limit: int = 50,
    offset: int = 0,
    q: str | None = None,
    dj: list[str] | None = None,
    genre: list[str] | None = None,
    event: list[str] | None = None,
    year: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Card/browse rows: lightweight, newest first, with optional filters.

    Filters are multi-select and OR within a facet / AND across facets: picking
    two DJs widens the result, picking a DJ and a genre narrows it. Array
    overlap (`&&`) is what makes the first part true, and it uses the existing
    GIN indexes on dj_names/genres.
    """
    from .scrobble.windows import cue_seconds

    where: list[str] = []
    params: list[Any] = []
    if q:
        where.append(
            "(title ILIKE %s OR event ILIKE %s "
            " OR EXISTS (SELECT 1 FROM unnest(dj_names) d WHERE d ILIKE %s))"
        )
        params += [f"%{q}%", f"%{q}%", f"%{q}%"]
    if dj:
        where.append("dj_names && %s")
        params.append(list(dj))
    if genre:
        where.append("genres && %s")
        params.append(list(genre))
    if event:
        where.append("event = ANY(%s)")
        params.append(list(event))
    if year:
        where.append("left(date_recorded, 4) = ANY(%s)")
        params.append(list(year))

    clause = ("WHERE " + " AND ".join(where)) if where else ""
    params += [limit, offset]
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT id, title, dj_names, event, date_recorded, genres, media_url,
                           jsonb_array_length(tracks), status, coverage, created_at,
                           {_LAST_CUE_SQL}
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
                "length_s": cue_seconds(r[11]),
            })
        return out
    finally:
        conn.close()


def facets() -> dict[str, list[dict[str, Any]]]:
    """Distinct DJs / genres / events / years with counts, for the filter chips.

    Aggregates over the whole table so the chips are correct regardless of which
    page is on screen. Cheap at this size; if the catalog reaches thousands this
    wants caching or a materialized view.
    """
    queries = {
        "djs": "SELECT d AS v, count(*) FROM setlists, unnest(dj_names) d GROUP BY d",
        "genres": "SELECT g AS v, count(*) FROM setlists, unnest(genres) g GROUP BY g",
        "events": (
            "SELECT event AS v, count(*) FROM setlists "
            "WHERE event IS NOT NULL AND event <> '' GROUP BY event"
        ),
        "years": (
            "SELECT left(date_recorded, 4) AS v, count(*) FROM setlists "
            "WHERE date_recorded IS NOT NULL AND date_recorded <> '' GROUP BY 1"
        ),
    }
    out: dict[str, list[dict[str, Any]]] = {}
    conn = _connect()
    try:
        with conn.cursor() as cur:
            for name, sql in queries.items():
                cur.execute(f"{sql} ORDER BY count(*) DESC, v ASC LIMIT 100")
                out[name] = [{"value": r[0], "count": r[1]} for r in cur.fetchall()]
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


# ---------------------------------------------------------------------------
# Job queue (jobs table) — a producer (API) enqueues; the worker claims with
# FOR UPDATE SKIP LOCKED so multiple workers never grab the same row.
# ---------------------------------------------------------------------------


def enqueue(
    type: str, setlist_id: str, payload: dict | None = None, priority: int = 0
) -> int:
    """Insert a queued job; return its id."""
    from psycopg.types.json import Jsonb

    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO jobs (type, setlist_id, payload, priority, status) "
                "VALUES (%s, %s, %s, %s, 'queued') RETURNING id",
                (type, setlist_id, Jsonb(payload) if payload is not None else None, priority),
            )
            job_id = cur.fetchone()[0]
        conn.commit()
        return job_id
    finally:
        conn.close()


def claim_job() -> dict[str, Any] | None:
    """Atomically claim the next queued job (highest priority, oldest first).

    Uses FOR UPDATE SKIP LOCKED: the row is locked for the life of the
    transaction, marked 'running', then committed — so a second worker polling
    concurrently skips it and takes the next one instead.
    """
    conn = _connect()
    try:
        # The row lock must survive until the UPDATE, so this one genuinely
        # needs a transaction — the pool runs autocommit by default.
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(
                "SELECT id, type, setlist_id, payload, attempts FROM jobs "
                "WHERE status = 'queued' "
                "ORDER BY priority DESC, created_at "
                "FOR UPDATE SKIP LOCKED LIMIT 1"
            )
            row = cur.fetchone()
            if row is None:
                return None
            cur.execute(
                "UPDATE jobs SET status = 'running', started_at = now(), "
                "attempts = attempts + 1 WHERE id = %s",
                (row[0],),
            )
        payload = json.loads(row[3]) if isinstance(row[3], str) else row[3]
        return {
            "id": row[0],
            "type": row[1],
            "setlist_id": str(row[2]) if row[2] else None,
            "payload": payload,
            "attempts": row[4] + 1,
        }
    finally:
        conn.close()


def complete_job(job_id: int) -> None:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE jobs SET status = 'done', finished_at = now() WHERE id = %s",
                (job_id,),
            )
        conn.commit()
    finally:
        conn.close()


def fail_job(job_id: int, error: str) -> None:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE jobs SET status = 'failed', error = %s, finished_at = now() "
                "WHERE id = %s",
                (error, job_id),
            )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Users (Last.fm identity). The session key is a permanent write credential:
# it authorizes scrobbling on that account forever and must never reach a
# browser. `get_user` therefore cannot return it — only `session_key_for` can,
# and that exists solely for the server-side scrobbler.
# ---------------------------------------------------------------------------


def upsert_user(lastfm_username: str, session_key: str) -> str:
    """Record a connected Last.fm account; returns the user id."""
    import uuid

    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (id, lastfm_username, lastfm_session_key)
                VALUES (%s, %s, %s)
                ON CONFLICT (lastfm_username) DO UPDATE SET
                    lastfm_session_key = EXCLUDED.lastfm_session_key
                RETURNING id
                """,
                (str(uuid.uuid4()), lastfm_username, session_key),
            )
            user_id = cur.fetchone()[0]
        conn.commit()
        return str(user_id)
    finally:
        conn.close()


def get_user(user_id: str) -> dict[str, Any] | None:
    """Public-safe user record. Never includes the Last.fm session key."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, lastfm_username, created_at FROM users WHERE id = %s", (user_id,)
            )
            row = cur.fetchone()
        if row is None:
            return None
        return {
            "id": str(row[0]),
            "lastfm_username": row[1],
            "created_at": str(row[2]) if row[2] else None,
        }
    finally:
        conn.close()


def get_scrobble_config(user_id: str) -> dict[str, Any]:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT scrobble_config FROM users WHERE id = %s", (user_id,))
            row = cur.fetchone()
        if row is None or row[0] is None:
            return {}
        return json.loads(row[0]) if isinstance(row[0], str) else row[0]
    finally:
        conn.close()


def set_scrobble_config(user_id: str, config: dict[str, Any]) -> None:
    from psycopg.types.json import Jsonb

    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET scrobble_config = %s WHERE id = %s", (Jsonb(config), user_id)
            )
        conn.commit()
    finally:
        conn.close()


def session_key_for(user_id: str) -> str | None:
    """The Last.fm session key, for signing scrobbles server-side. Never serve this."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT lastfm_session_key FROM users WHERE id = %s", (user_id,))
            row = cur.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Captured source pages (raw_pages) — the maintainer scrapes on their own
# machine and uploads the HTML here; the server reads it back instead of
# running a browser. Stored gzipped: pages are ~1-2MB of repetitive markup.
# ---------------------------------------------------------------------------


def put_page(url: str, html: str) -> int:
    """Store (or replace) the captured HTML for a URL. Returns compressed size."""
    import gzip

    from .fetcher import url_digest

    blob = gzip.compress(html.encode("utf-8"))
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO raw_pages (url_digest, url, html, byte_size, fetched_at)
                VALUES (%s, %s, %s, %s, now())
                ON CONFLICT (url_digest) DO UPDATE SET
                    url = EXCLUDED.url, html = EXCLUDED.html,
                    byte_size = EXCLUDED.byte_size, fetched_at = now()
                """,
                (url_digest(url), url, blob, len(blob)),
            )
        conn.commit()
        return len(blob)
    finally:
        conn.close()


def get_page(url: str) -> str | None:
    """Read back captured HTML for a URL, or None if it was never captured."""
    import gzip

    from .fetcher import url_digest

    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT html FROM raw_pages WHERE url_digest = %s", (url_digest(url),))
            row = cur.fetchone()
        if row is None:
            return None
        return gzip.decompress(bytes(row[0])).decode("utf-8")
    finally:
        conn.close()


def list_pages() -> list[dict[str, Any]]:
    """Captured pages, newest first (maintainer visibility)."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT url, byte_size, fetched_at FROM raw_pages ORDER BY fetched_at DESC"
            )
            rows = cur.fetchall()
        return [{"url": r[0], "byte_size": r[1], "fetched_at": str(r[2])} for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Track registry (tracks table) — the server-side backing store for the
# cross-set cache. See registry_pg.PgRegistry, which owns the dedupe logic.
# ---------------------------------------------------------------------------

_TRACK_COLUMNS = (
    "id, artists, title, remix, is_unreleased, spotify_id, youtube_id, "
    "lastfm_artist, lastfm_track, mbid, created_at, updated_at"
)


def all_tracks() -> list["TrackRecord"]:
    """Every canonical track record (parsed_key is recomputed in memory)."""
    from .models import TrackRecord

    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {_TRACK_COLUMNS} FROM tracks")
            rows = cur.fetchall()
        return [
            TrackRecord(
                id=str(r[0]),
                artists=list(r[1] or []),
                title=r[2],
                remix=r[3],
                is_unreleased=r[4],
                spotify_id=r[5],
                youtube_id=r[6],
                lastfm_artist=r[7],
                lastfm_track=r[8],
                mbid=r[9],
                created_at=str(r[10]),
                updated_at=str(r[11]),
            )
            for r in rows
        ]
    finally:
        conn.close()


def upsert_track(rec: "TrackRecord") -> None:
    """Write one canonical record, keyed by its (in-memory-deduped) id.

    parsed_key is stored so the plain btree index can serve pre-search lookups;
    it is computed the same way the registry indexes it in memory.
    """
    from .registry import parsed_key as _parsed_key

    key = _parsed_key(rec.artists, rec.title, rec.remix)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tracks (id, artists, title, remix, is_unreleased,
                                    spotify_id, youtube_id, lastfm_artist,
                                    lastfm_track, mbid, parsed_key)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    artists = EXCLUDED.artists, title = EXCLUDED.title,
                    remix = EXCLUDED.remix, is_unreleased = EXCLUDED.is_unreleased,
                    spotify_id = EXCLUDED.spotify_id, youtube_id = EXCLUDED.youtube_id,
                    lastfm_artist = EXCLUDED.lastfm_artist,
                    lastfm_track = EXCLUDED.lastfm_track, mbid = EXCLUDED.mbid,
                    parsed_key = EXCLUDED.parsed_key, updated_at = now()
                """,
                (
                    rec.id, rec.artists, rec.title, rec.remix, rec.is_unreleased,
                    rec.spotify_id, rec.youtube_id, rec.lastfm_artist,
                    rec.lastfm_track, rec.mbid, key,
                ),
            )
        conn.commit()
    finally:
        conn.close()
