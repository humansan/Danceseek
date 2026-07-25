"""Platform selection: the Last.fm-only path does less work, honestly.

A disabled platform must cost nothing (no client, no search, no candidate lines
in the LLM prompt) and must not be counted against a track's status.
"""

import pytest

from soundseek.models import ParserInfo, Resolution, Setlist, SetlistTrack
from soundseek.resolver import resolve as R
from soundseek.resolver.gather import Unit, UnitCandidates, gather
from soundseek.resolver.picker import apply_pick


class _FakeSpotify:
    def search(self, q, limit=5):
        return [{"id": "sp1", "title": "T", "artists": ["A"], "url": "u", "duration_ms": 1}]


class _FakeYouTube:
    def search(self, q, limit=5):
        return [{"id": "yt1", "title": "T", "artists": ["chan"], "url": "u", "duration_ms": 1}]


class _FakeLastfm:
    def get_info(self, artist, track):
        return {"artist": "A", "track": "T", "listeners": 10, "mbid": None, "url": "u"}

    def search(self, title, artist=None, limit=5):
        return [{"artist": "A", "track": "T"}]


@pytest.fixture
def clients(monkeypatch):
    """_Clients with the network clients stubbed out."""
    monkeypatch.setattr(R, "SpotifyClient", _FakeSpotify)
    monkeypatch.setattr(R, "YouTubeSearch", _FakeYouTube)
    monkeypatch.setattr(R, "LastfmClient", _FakeLastfm)

    def _make(platforms=None):
        return R._Clients(R.ResolveSummary(), platforms)

    return _make


# --- which clients get built ------------------------------------------------


def test_default_enables_every_platform(clients):
    c = clients()
    assert c.spotify and c.youtube and c.lastfm
    assert sorted(c.active()) == ["lastfm", "spotify", "youtube"]


def test_lastfm_only_builds_nothing_else(clients):
    c = clients(R.LASTFM_ONLY)
    assert c.lastfm and c.spotify is None and c.youtube is None
    assert c.active() == ["lastfm"]
    assert c.applicable(Unit(["A"], "T", None, "A - T")) == ["lastfm"]


def test_unknown_platform_is_rejected(clients):
    with pytest.raises(ValueError, match="Unknown platform"):
        clients(["soundcloud"])


def test_empty_platform_list_is_rejected(clients):
    with pytest.raises(ValueError, match="At least one"):
        clients([])


# --- what that saves --------------------------------------------------------


def test_lastfm_only_skips_the_spotify_and_youtube_searches(clients):
    unit = Unit(["A"], "T", None, "A - T")
    full = gather(unit, clients(), limit=3)
    lean = gather(unit, clients(R.LASTFM_ONLY), limit=3)

    assert full.spotify and full.youtube and full.lastfm
    assert lean.lastfm  # the one that matters for scrobbling
    assert lean.spotify == [] and lean.youtube == []
    # No Spotify/YouTube queries were even issued.
    assert "spotify_query" not in lean.log and "youtube_query" not in lean.log


def test_a_lastfm_match_alone_counts_as_resolved_in_lastfm_only_mode():
    """With only Last.fm applicable, a Last.fm hit is a complete result —
    not 'partial' for missing the platforms we never asked about."""
    uc = UnitCandidates(unit=Unit(["A"], "T", None, "A - T"))
    uc.lastfm = [{"artist": "A", "track": "T", "listeners": 9, "mbid": None, "url": "u"}]
    pick = type("P", (), {"spotify": None, "youtube": None, "lastfm": "L1", "confidence": 0.9})()

    lean = apply_pick(uc, pick, ["lastfm"])
    assert lean.status == "resolved" and lean.lastfm.artist == "A"

    full = apply_pick(uc, pick, ["spotify", "youtube", "lastfm"])
    assert full.status == "partial"  # same pick, but two platforms went unmatched


# --- mashup rows ------------------------------------------------------------


def _mashup_setlist() -> Setlist:
    return Setlist(
        source_url="https://example/tracklist/x/y.html",
        parser=ParserInfo(model="test"),
        tracks=[
            SetlistTrack(
                position=1,
                raw_text="A - X vs. B - Y",
                artists=["A"],
                title="X",
                mashup_components=[
                    {"artists": ["A"], "title": "X"},
                    {"artists": ["B"], "title": "Y"},
                ],
            )
        ],
    )


class _NullRegistry:
    """Registry that never has a cached answer."""

    def lookup(self, artists, title, remix):
        return None

    def resolution_from_record(self, record):
        return None


def test_mashup_rows_are_settled_without_an_llm_call_when_youtube_is_off(clients, tmp_path):
    """Whole-mashup matching is YouTube-only. On a Last.fm-only run there is
    nothing to search an 'A vs. B' blob against, so it settles immediately as
    no_match instead of burning a search + LLM slot. Its components still go
    to the LLM — those are what the scrobbler uses."""
    setlist = _mashup_setlist()
    summary = R.ResolveSummary()
    pending = R._collect_pending(
        setlist, _NullRegistry(), clients(R.LASTFM_ONLY), summary,
        force=False, limit=None, log_path=tmp_path / "log.jsonl",
    )

    assert setlist.tracks[0].resolution is not None
    assert setlist.tracks[0].resolution.status == "no_match"
    assert setlist.tracks[0].resolution.method == "skip"
    assert summary.no_match == 1
    # Only the two components are queued for searching.
    assert [p.unit.kind for p in pending] == ["component", "component"]


def test_mashup_rows_still_reach_the_llm_on_a_full_run(clients, tmp_path):
    setlist = _mashup_setlist()
    pending = R._collect_pending(
        setlist, _NullRegistry(), clients(), R.ResolveSummary(),
        force=False, limit=None, log_path=tmp_path / "log.jsonl",
    )
    assert setlist.tracks[0].resolution is None
    assert [p.unit.kind for p in pending] == ["mashup_row", "component", "component"]


# --- coverage ---------------------------------------------------------------


def test_coverage_records_which_platforms_were_searched():
    setlist = Setlist(
        source_url="https://example/tracklist/x/y.html",
        parser=ParserInfo(model="test"),
        tracks=[
            SetlistTrack(
                position=1, raw_text="A - T", artists=["A"], title="T",
                resolution=Resolution(status="resolved"),
            )
        ],
    )
    summary = R.ResolveSummary(resolved=1, platforms=["lastfm"])
    coverage = R.build_coverage(setlist, summary)

    # spotify=0 here means "never asked", and the platforms key says so.
    assert coverage["platforms"] == ["lastfm"]
    assert coverage["spotify"] == 0 and coverage["resolved"] == 1
