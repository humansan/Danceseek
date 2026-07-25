# The Danceseek API. Read-only over Neon: browse, cue windows, Last.fm identity
# and scrobbling.
#
# It deliberately installs *only* the `api` group — no Playwright, no LangChain,
# no yt-dlp. Ingestion runs on the maintainer's machine (see apps/ingest), so
# none of that belongs in a server image. tests/test_api_surface.py asserts the
# API never imports the scraper or the pipeline, which is what keeps this true.

FROM python:3.12-slim

# uv gives us the same resolver and lockfile the project develops against.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Dependencies first, so a code change doesn't re-resolve the world.
# (No README here — pyproject declares none, and the repo's lives in gitignored docs/.)
COPY pyproject.toml uv.lock ./
COPY src/ ./src/
RUN uv sync --frozen --no-default-groups --group api

COPY apps/api/ ./apps/api/
COPY migrations/ ./migrations/
COPY alembic.ini ./

ENV PATH="/app/.venv/bin:$PATH" \
    SOUNDSEEK_STORE_BACKEND=postgres \
    SOUNDSEEK_FETCH_BACKEND=stored

EXPOSE 8000
# Platforms inject $PORT; default to 8000 for a plain `docker run`.
CMD ["sh", "-c", "uvicorn apps.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
