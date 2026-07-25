"""The deployed API is a read layer — it must not grow ingestion back.

Ingestion (browser + LLM + platform searches) lives in apps/ingest and runs on
the maintainer's machine. If an ingest route reappears here, the server has
quietly taken on a browser dependency and a Cloudflare problem.
"""

import pytest

pytest.importorskip("fastapi")

import apps.api.main as main  # noqa: E402

PATHS = set(main.app.openapi()["paths"])


def test_the_surface_is_reads_plus_identity():
    """Reads over the catalog, plus Last.fm sign-in. Nothing that scrapes or
    normalizes — if that changes, it should be a decision, not a drift."""
    assert PATHS == {
        "/health",
        "/setlists",
        "/facets",
        "/setlists/{setlist_id}",
        "/setlists/{setlist_id}/cues",
        "/setlists/{setlist_id}/export",
        "/me",
        "/auth/lastfm/start",
        "/auth/lastfm/callback",
        "/auth/lastfm/complete",
        "/auth/logout",
        "/me/scrobble-config",
        "/scrobble",
        "/scrobble/now-playing",
        "/setlists/{setlist_id}/scrobble-set",
    }


def test_no_ingest_or_admin_routes():
    assert not [p for p in PATHS if "admin" in p]
    # POST /setlists (the add flow) moved to the local console.
    assert "post" not in main.app.openapi()["paths"]["/setlists"]


def test_the_api_module_does_not_import_the_pipeline():
    """Importing fetcher/pipeline here would drag Playwright and LangChain into
    the deployed image."""
    source = (main.__file__ and open(main.__file__, encoding="utf-8").read()) or ""
    assert "import pipeline" not in source
    assert "fetcher" not in source
