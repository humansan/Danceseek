"""Export-preview endpoint. Needs the `api` dependency-group (fastapi); the
default `uv run pytest` skips this module. db.get_by_id is monkeypatched so the
endpoint never touches Neon."""

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

import apps.api.main as main  # noqa: E402
from soundseek import db  # noqa: E402
from soundseek.models import (  # noqa: E402
    ParserInfo,
    PlatformMatch,
    Resolution,
    Setlist,
    SetlistTrack,
)

client = TestClient(main.app)

_META = {"status": "resolved", "coverage": None, "resolved_at": None, "created_at": None}


def _setlist_youtube_only():
    return Setlist(
        source_url="u",
        title="t",
        parser=ParserInfo(model="x"),
        tracks=[
            SetlistTrack(
                position=1,
                raw_text="A - B",
                artists=["A"],
                title="B",
                resolution=Resolution(
                    status="partial",
                    youtube=PlatformMatch(id="yt1", title="B", artists=["A"], url="u"),
                ),
            )
        ],
    )


def test_export_preview_youtube(monkeypatch):
    monkeypatch.setattr(db, "get_by_id", lambda sid: (_setlist_youtube_only(), _META))
    r = client.post("/setlists/abc/export", json={"target": "youtube"})
    assert r.status_code == 200
    body = r.json()
    assert body["target"] == "youtube"
    assert body["added"] == 1
    assert body["items"][0]["id"] == "yt1"


def test_export_preview_spotify_no_match(monkeypatch):
    monkeypatch.setattr(db, "get_by_id", lambda sid: (_setlist_youtube_only(), _META))
    r = client.post("/setlists/abc/export", json={"target": "spotify"})
    assert r.status_code == 200
    body = r.json()
    assert body["added"] == 0
    assert body["skipped"] and body["skipped"][0]["reason"] == "no_match"


def test_export_preview_unknown_id_404(monkeypatch):
    monkeypatch.setattr(db, "get_by_id", lambda sid: None)
    r = client.post("/setlists/abc/export", json={"target": "youtube"})
    assert r.status_code == 404


def test_export_preview_bad_target_422(monkeypatch):
    monkeypatch.setattr(db, "get_by_id", lambda sid: (_setlist_youtube_only(), _META))
    r = client.post("/setlists/abc/export", json={"target": "soundcloud"})
    assert r.status_code == 422


# --- cue windows ------------------------------------------------------------


def _setlist_with_cues():
    return Setlist(
        source_url="u", title="t", parser=ParserInfo(model="x"),
        tracks=[
            SetlistTrack(position=1, cue_time="0:00", raw_text="A - B", artists=["A"], title="B"),
            SetlistTrack(position=2, cue_time="5:00", raw_text="C - D", artists=["C"], title="D"),
        ],
    )


def test_cues_endpoint_returns_windows(monkeypatch):
    monkeypatch.setattr(db, "get_by_id", lambda sid: (_setlist_with_cues(), _META))
    r = client.get("/setlists/abc/cues?duration=900")
    assert r.status_code == 200
    body = r.json()
    assert body["timing"] == "cue" and body["live_capable"] is True
    assert [(w["start_s"], w["end_s"]) for w in body["windows"]] == [(0, 300), (300, 900)]
    assert body["windows"][0]["label"] == "A – B"


def test_cues_endpoint_works_without_a_duration(monkeypatch):
    monkeypatch.setattr(db, "get_by_id", lambda sid: (_setlist_with_cues(), _META))
    assert client.get("/setlists/abc/cues").status_code == 200


def test_cues_endpoint_404s_for_an_unknown_setlist(monkeypatch):
    monkeypatch.setattr(db, "get_by_id", lambda sid: None)
    assert client.get("/setlists/abc/cues").status_code == 404
