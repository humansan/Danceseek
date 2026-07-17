"""Alembic environment. Runs SQL migrations against DATABASE_URL.

The application data layer uses plain psycopg (see soundseek/db.py); Alembic is
used only to version the schema. Migrations are written as raw SQL via
op.execute, so no SQLAlchemy models are needed here.
"""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import engine_from_config, pool

# Load .env so DATABASE_URL is available when running locally.
try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv is optional in prod
    pass

config = context.config

_db_url = os.environ.get("DATABASE_URL")
if _db_url:
    # psycopg3 driver
    config.set_main_option("sqlalchemy.url", _db_url.replace("postgresql://", "postgresql+psycopg://", 1))


def run_migrations_offline() -> None:
    context.configure(url=config.get_main_option("sqlalchemy.url"), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
