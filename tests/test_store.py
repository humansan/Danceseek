import pytest

# Test the JSON backend directly: `store` dispatches to json or postgres based
# on SOUNDSEEK_STORE_BACKEND, and these cases assert JSON-file semantics (and
# must never touch the real Neon DB).
from soundseek import store_json as store
from soundseek.config import settings
from soundseek.models import ParserInfo, Setlist, SetlistTrack


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path):
    original = settings.data_dir
    settings.data_dir = tmp_path
    yield
    settings.data_dir = original


def _setlist(url="https://example/tracklist/a/b.html"):
    return Setlist(
        source_url=url,
        title="Test Set",
        parser=ParserInfo(model="test"),
        tracks=[SetlistTrack(position=1, raw_text="A - B", artists=["A"], title="B")],
    )


def test_save_and_load_round_trip():
    s = _setlist()
    store.save(s)
    assert store.lookup(s.source_url) == s.id
    loaded = store.load(s.id)
    assert loaded == s


def test_load_by_url_missing_returns_none():
    assert store.load_by_url("https://example/tracklist/nope/x.html") is None


def test_reingest_replaces_previous_record():
    first = _setlist()
    store.save(first)
    second = _setlist()  # same URL, new id
    store.save(second)

    assert store.lookup(first.source_url) == second.id
    assert not store.setlist_path(first.id).exists()
    assert len(store.list_all()) == 1


def test_list_all_newest_first():
    a = _setlist("https://example/tracklist/a/a.html")
    a.scraped_at = "2026-01-01T00:00:00+00:00"
    b = _setlist("https://example/tracklist/b/b.html")
    b.scraped_at = "2026-06-01T00:00:00+00:00"
    store.save(a)
    store.save(b)
    assert [s.id for s in store.list_all()] == [b.id, a.id]
