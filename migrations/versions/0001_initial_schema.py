"""Initial production schema.

Additive and idempotent so it coexists with the pre-existing single-table
`setlists` (and its data) from the Step 3-lite prototype: existing columns are
kept, new columns are ADD COLUMN IF NOT EXISTS, and the relational tables use
CREATE TABLE IF NOT EXISTS.

Revision ID: 0001
Revises:
Create Date: 2026-07-17
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


UPGRADE_SQL = """
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- setlists: the cache prototype already created this; ensure it exists, then
-- add the production columns.
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
);
ALTER TABLE setlists ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'resolved';
ALTER TABLE setlists ADD COLUMN IF NOT EXISTS coverage JSONB;
ALTER TABLE setlists ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ;
ALTER TABLE setlists ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now();

CREATE INDEX IF NOT EXISTS ix_setlists_created_at ON setlists (created_at DESC);
CREATE INDEX IF NOT EXISTS ix_setlists_date_recorded ON setlists (date_recorded DESC);
CREATE INDEX IF NOT EXISTS ix_setlists_status ON setlists (status);
CREATE INDEX IF NOT EXISTS ix_setlists_dj_names ON setlists USING GIN (dj_names);
CREATE INDEX IF NOT EXISTS ix_setlists_genres ON setlists USING GIN (genres);
CREATE INDEX IF NOT EXISTS ix_setlists_title_trgm ON setlists USING GIN (title gin_trgm_ops);
CREATE INDEX IF NOT EXISTS ix_setlists_event_trgm ON setlists USING GIN (event gin_trgm_ops);

-- tracks: the canonical registry (ports data/tracks.json). Dedupe keys mirror
-- registry.py: spotify_id, (lastfm_artist, lastfm_track), normalized parsed key.
CREATE TABLE IF NOT EXISTS tracks (
    id             UUID PRIMARY KEY,
    artists        TEXT[] NOT NULL DEFAULT '{}',
    title          TEXT,
    remix          TEXT,
    is_unreleased  BOOLEAN NOT NULL DEFAULT FALSE,
    spotify_id     TEXT,
    youtube_id     TEXT,
    lastfm_artist  TEXT,
    lastfm_track   TEXT,
    mbid           TEXT,
    parsed_key     TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_tracks_spotify_id ON tracks (spotify_id) WHERE spotify_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_tracks_lastfm ON tracks (lower(lastfm_artist), lower(lastfm_track))
    WHERE lastfm_artist IS NOT NULL AND lastfm_track IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_tracks_parsed_key ON tracks (parsed_key);

-- jobs: the Neon-backed queue (polled with FOR UPDATE SKIP LOCKED).
CREATE TABLE IF NOT EXISTS jobs (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    type          TEXT NOT NULL,
    setlist_id    UUID REFERENCES setlists(id) ON DELETE CASCADE,
    status        TEXT NOT NULL DEFAULT 'queued',
    priority      INT NOT NULL DEFAULT 0,
    attempts      INT NOT NULL DEFAULT 0,
    payload       JSONB,
    error         TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at    TIMESTAMPTZ,
    finished_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_jobs_poll ON jobs (status, priority DESC, created_at) WHERE status = 'queued';

-- users: light Last.fm identity.
CREATE TABLE IF NOT EXISTS users (
    id                 UUID PRIMARY KEY,
    lastfm_username    TEXT NOT NULL UNIQUE,
    lastfm_session_key TEXT NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- oauth_tokens: per-user Spotify/YouTube tokens for export.
CREATE TABLE IF NOT EXISTS oauth_tokens (
    user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider      TEXT NOT NULL,
    access_token  TEXT NOT NULL,
    refresh_token TEXT,
    expires_at    TIMESTAMPTZ,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, provider)
);

-- saved_sets & exports: personal library.
CREATE TABLE IF NOT EXISTS saved_sets (
    user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    setlist_id UUID NOT NULL REFERENCES setlists(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, setlist_id)
);

CREATE TABLE IF NOT EXISTS exports (
    id           UUID PRIMARY KEY,
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    setlist_id   UUID NOT NULL REFERENCES setlists(id) ON DELETE CASCADE,
    provider     TEXT NOT NULL,
    playlist_url TEXT NOT NULL,
    added_count  INT NOT NULL DEFAULT 0,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    # Only drop the tables this migration introduced; leave setlists in place.
    op.execute(
        "DROP TABLE IF EXISTS exports, saved_sets, oauth_tokens, users, jobs, tracks CASCADE;"
    )
