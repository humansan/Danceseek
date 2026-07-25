"""Drop the scrobbles table.

It existed only to dedupe: to stop a seek-back or a reload from logging a track
twice. But replaying a track *is* a second listen, and scrobbling it again is
the honest outcome — so the table was buying a guarantee we didn't want, at the
cost of storing a row per play per user.

What replaces it: nothing on the server. The browser tracks what it has already
logged during the current page session (enough to stop a single window from
firing twice while you sit in it), and a genuine replay produces a new
timestamp, which is exactly the record it should produce.

`users.scrobble_config` stays — four enum values of user preference, needed
server-side because the server decides eligibility.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-25
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS scrobbles;")


def downgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS scrobbles (
            id               UUID PRIMARY KEY,
            user_id          UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            setlist_id       UUID NOT NULL REFERENCES setlists(id) ON DELETE CASCADE,
            position         INT NOT NULL,
            component_index  INT NOT NULL DEFAULT -1,
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
        """
    )
