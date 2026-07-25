"""Captured source pages (maintainer ingest path).

Scraping 1001tracklists needs a real browser with a Cloudflare clearance
profile, which only the maintainer's machine has. That machine captures the
HTML and uploads it here; the server's worker reads it back
(SOUNDSEEK_FETCH_BACKEND=stored) and runs the normal process job, so
production keeps a single ingest code path and needs no browser.

HTML is stored gzip-compressed: raw tracklist pages are ~1-2 MB of very
repetitive markup and compress to roughly a tenth of that.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-24
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


UPGRADE_SQL = """
CREATE TABLE IF NOT EXISTS raw_pages (
    url_digest  TEXT PRIMARY KEY,          -- fetcher.url_digest(url)
    url         TEXT NOT NULL UNIQUE,
    html        BYTEA NOT NULL,            -- gzip-compressed UTF-8
    byte_size   INT NOT NULL DEFAULT 0,    -- compressed size, for eyeballing growth
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_raw_pages_fetched_at ON raw_pages (fetched_at DESC);
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS raw_pages;")
