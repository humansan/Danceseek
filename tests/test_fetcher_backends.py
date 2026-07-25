"""Fetch backend dispatch: local (browser here) vs stored (server) vs managed.

No browser and no database are touched — the point of the seam is that the
choice is made before either is reached.
"""

import pytest

from soundseek import db, fetcher
from soundseek.config import settings

URL = "https://www.1001tracklists.com/tracklist/abc/some-dj-somewhere-2026.html"


@pytest.fixture
def backend(monkeypatch):
    def _set(name):
        monkeypatch.setattr(settings, "fetch_backend", name)

    return _set


def test_local_backend_uses_the_browser_fetcher(backend, monkeypatch):
    backend("local")
    monkeypatch.setattr(fetcher, "fetch_local", lambda url, force=False: f"<html>{url}</html>")
    assert fetcher.fetch(URL) == f"<html>{URL}</html>"


def test_stored_backend_reads_the_captured_page(backend, monkeypatch):
    backend("stored")
    monkeypatch.setattr(db, "get_page", lambda url: "<html>captured</html>")
    assert fetcher.fetch(URL) == "<html>captured</html>"


def test_stored_backend_explains_itself_when_nothing_was_captured(backend, monkeypatch):
    backend("stored")
    monkeypatch.setattr(db, "get_page", lambda url: None)
    with pytest.raises(fetcher.FetchError, match="soundseek publish"):
        fetcher.fetch(URL)


def test_stored_backend_still_validates_the_url(backend):
    backend("stored")
    with pytest.raises(fetcher.FetchError, match="Not a 1001tracklists"):
        fetcher.fetch("https://example.com/nope")


def test_managed_backend_is_not_implemented_yet(backend):
    backend("managed")
    with pytest.raises(fetcher.FetchError, match="not implemented"):
        fetcher.fetch(URL)


def test_unknown_backend_names_the_valid_options(backend):
    backend("wishful")
    with pytest.raises(fetcher.FetchError, match="'local', 'stored', or 'managed'"):
        fetcher.fetch(URL)


def test_url_digest_is_stable_and_short():
    assert fetcher.url_digest(URL) == fetcher.url_digest(URL)
    assert len(fetcher.url_digest(URL)) == 16
    assert fetcher.url_digest(URL) != fetcher.url_digest(URL + "x")
