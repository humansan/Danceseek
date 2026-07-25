"""Scrobble log + per-user scrobble settings.

The unique index is the point of this table: seeking backwards, refreshing the
page, or a duplicated request must not double-scrobble a track. `session_id` is
minted client-side per playback, so re-watching a set later is a new session and
scrobbles again — which is what a listener expects.

component_index defaults to -1 rather than NULL because NULLs don't compare
equal in a unique index, which would let a mashup component scrobble twice.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-25
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


UPGRADE_SQL = """
ALTER TABLE users ADD COLUMN IF NOT EXISTS scrobble_config JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE TABLE IF NOT EXISTS scrobbles (
    id               UUID PRIMARY KEY,
    user_id          UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    setlist_id       UUID NOT NULL REFERENCES setlists(id) ON DELETE CASCADE,
    position         INT NOT NULL,
    component_index  INT NOT NULL DEFAULT -1,   -- -1 = the row itself
    session_id       UUID NOT NULL,
    artist           TEXT NOT NULL,
    track            TEXT NOT NULL,
    played_at        TIMESTAMPTZ NOT NULL,
    accepted         BOOLEAN NOT NULL DEFAULT TRUE,
    error            TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_scrobbles_once
    ON scrobbles (user_id, setlist_id, position, component_index, session_id);
CREATE INDEX IF NOT EXISTS ix_scrobbles_user ON scrobbles (user_id, played_at DESC);
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS scrobbles;")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS scrobble_config;")
