"""build_setlist() fetches+normalizes without persisting; ingest() still saves.

The worker relies on build_setlist NOT saving so it can assign the stub row's id
before store.save (otherwise the client's id would 404)."""

import pytest

from soundseek import pipeline
from soundseek.models import ParserInfo, Setlist


@pytest.fixture
def stubbed(monkeypatch):
    """Stub fetch/extract/normalize/_assemble; capture store.save calls."""
    result = Setlist(source_url="http://x", parser=ParserInfo(model="test"), tracks=[])
    saves: list[Setlist] = []
    monkeypatch.setattr(pipeline.fetcher, "fetch", lambda url, force=False: "<html>")
    monkeypatch.setattr(pipeline, "extract", lambda html, url: object())
    monkeypatch.setattr(pipeline, "normalize", lambda page: [])
    monkeypatch.setattr(pipeline, "_assemble", lambda page, tracks: result)
    monkeypatch.setattr(pipeline.store, "save", lambda s: saves.append(s))
    monkeypatch.setattr(pipeline.store, "load_by_url", lambda url: None)
    return result, saves


def test_build_setlist_does_not_persist(stubbed):
    result, saves = stubbed
    out = pipeline.build_setlist("http://x")
    assert out is result
    assert saves == []


def test_ingest_persists(stubbed):
    result, saves = stubbed
    out = pipeline.ingest("http://x")
    assert out is result
    assert saves == [result]
