"""Round-trip tests for the pure row helpers (no live database)."""

from soundseek.db import _COLUMNS, from_row, to_row
from soundseek.models import (
    LastfmMatch,
    ParserInfo,
    PlatformMatch,
    Resolution,
    Setlist,
    SetlistTrack,
)


def _setlist() -> Setlist:
    return Setlist(
        source_url="https://example/tracklist/a/b.html",
        title="Test @ Somewhere 2026-01-01",
        dj_names=["Test DJ"],
        event="Somewhere",
        date_recorded="2026-01-01",
        genres=["Techno"],
        media_url="https://www.youtube.com/watch?v=abc123",
        media_kind="youtube",
        parser=ParserInfo(model="test-model"),
        tracks=[
            SetlistTrack(
                position=1,
                source_track_number=1,
                cue_time="0:00",
                raw_text="A & B - Song (X Remix)",
                artists=["A", "B"],
                title="Song",
                remix="X Remix",
                resolution=Resolution(
                    status="resolved",
                    spotify=PlatformMatch(id="sp1", title="Song", artists=["A"], url="u"),
                    lastfm=LastfmMatch(artist="A", track="Song", listeners=42),
                    confidence=0.9,
                ),
            ),
            SetlistTrack(position=2, raw_text="ID - ID", is_id=True),
        ],
    )


def _simulate_db_row(params: dict) -> tuple:
    """What psycopg would hand back on SELECT: Jsonb params come back as objects."""
    keys = [k.strip() for k in _COLUMNS.split(",")]
    row = []
    for k in keys:
        v = params[k]
        row.append(v.obj if hasattr(v, "obj") else v)  # unwrap Jsonb
    return tuple(row)


def test_row_round_trip_is_lossless():
    original = _setlist()
    row = _simulate_db_row(to_row(original))
    rebuilt = from_row(row)
    assert rebuilt == original


def test_round_trip_preserves_nested_resolution():
    rebuilt = from_row(_simulate_db_row(to_row(_setlist())))
    res = rebuilt.tracks[0].resolution
    assert res.spotify.id == "sp1"
    assert res.lastfm.artist == "A" and res.lastfm.listeners == 42
    assert rebuilt.tracks[1].is_id and rebuilt.tracks[1].resolution is None


def test_round_trip_media_fields():
    rebuilt = from_row(_simulate_db_row(to_row(_setlist())))
    assert rebuilt.media_url == "https://www.youtube.com/watch?v=abc123"
    assert rebuilt.media_kind == "youtube"
