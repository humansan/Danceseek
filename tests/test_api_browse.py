"""Browse: multi-select filtering, search, set length, facets.

The SQL itself is exercised against a fake cursor — these assert the query
*shape* (which clauses and parameters are built) and the row mapping, without
touching Neon.
"""

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

import apps.api.main as main  # noqa: E402
from soundseek import db  # noqa: E402

client = TestClient(main.app)


class _Cursor:
    """Records the SQL it was given and replays canned rows."""

    def __init__(self, calls, rows):
        self._calls = calls
        self._rows = rows

    def execute(self, sql, params=None):
        self._calls.append((" ".join(sql.split()), params))

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Conn:
    def __init__(self, calls, rows):
        self._calls = calls
        self._rows = rows

    def cursor(self):
        return _Cursor(self._calls, self._rows)

    def commit(self):
        pass

    def close(self):
        pass


def _row(**kw):
    base = {
        "id": "11111111-1111-1111-1111-111111111111", "title": "A set",
        "dj_names": ["ISOKNOCK"], "event": "EDC", "date_recorded": "2025-05-17",
        "genres": ["Bass House"], "media_url": "https://youtu.be/abc",
        "track_count": 45, "status": "resolved", "coverage": None,
        "created_at": "2026-07-25", "last_cue": "1:01:46",
    }
    base.update(kw)
    return tuple(base.values())


@pytest.fixture
def sql(monkeypatch):
    """Capture the SQL list_summaries/facets build, with one canned row."""
    calls = []
    monkeypatch.setattr(db, "_connect", lambda: _Conn(calls, [_row()]))
    return calls


# --- filtering ---------------------------------------------------------------


def test_no_filters_builds_no_where_clause(sql):
    db.list_summaries()
    statement, params = sql[0]
    # The last-cue subquery has a WHERE of its own; the filter clause is the one
    # attached to `FROM setlists`.
    assert "FROM setlists WHERE" not in statement
    assert params == [50, 0]


def test_multiple_djs_widen_the_result_via_array_overlap(sql):
    """Two DJs must mean "either", not "both" — hence && rather than ANY."""
    db.list_summaries(dj=["ISOKNOCK", "Armin van Buuren"])
    statement, params = sql[0]
    assert "dj_names && %s" in statement
    assert params[0] == ["ISOKNOCK", "Armin van Buuren"]


def test_filters_across_facets_are_combined_with_and(sql):
    db.list_summaries(dj=["ISOKNOCK"], genre=["Bass House"])
    statement, _ = sql[0]
    assert statement.count(" AND ") == 1
    assert "dj_names && %s" in statement and "genres && %s" in statement


def test_year_filters_on_the_date_prefix(sql):
    db.list_summaries(year=["2025"])
    statement, params = sql[0]
    assert "left(date_recorded, 4) = ANY(%s)" in statement
    assert params[0] == ["2025"]


def test_event_filter_is_exact(sql):
    db.list_summaries(event=["EDC Las Vegas"])
    statement, params = sql[0]
    assert "event = ANY(%s)" in statement and params[0] == ["EDC Las Vegas"]


def test_search_also_matches_a_dj_name(sql):
    """Searching "knock" should find ISOKNOCK's sets, not just titles."""
    db.list_summaries(q="knock")
    statement, params = sql[0]
    assert "unnest(dj_names)" in statement
    assert params[:3] == ["%knock%", "%knock%", "%knock%"]


# --- row mapping -------------------------------------------------------------


def test_set_length_is_parsed_from_the_last_cue(sql):
    row = db.list_summaries()[0]
    assert row["length_s"] == 3706  # 1:01:46
    assert row["track_count"] == 45


def test_a_set_with_no_cues_has_no_length(monkeypatch):
    monkeypatch.setattr(db, "_connect", lambda: _Conn([], [_row(last_cue=None)]))
    assert db.list_summaries()[0]["length_s"] is None


# --- facets ------------------------------------------------------------------


def test_facets_group_and_count_each_dimension(monkeypatch):
    calls = []
    monkeypatch.setattr(db, "_connect", lambda: _Conn(calls, [("ISOKNOCK", 3)]))
    result = db.facets()

    assert set(result) == {"djs", "genres", "events", "years"}
    assert result["djs"] == [{"value": "ISOKNOCK", "count": 3}]
    assert all("ORDER BY count(*) DESC" in stmt for stmt, _ in calls)
    # Blank events/years would render as empty chips.
    assert any("event <> ''" in stmt for stmt, _ in calls)


# --- endpoints ---------------------------------------------------------------


def test_the_facets_endpoint_shapes_the_response(monkeypatch):
    monkeypatch.setattr(
        db, "facets",
        lambda: {"djs": [{"value": "ISOKNOCK", "count": 3}], "genres": [], "events": [], "years": []},
    )
    body = client.get("/facets").json()
    assert body["djs"] == [{"value": "ISOKNOCK", "count": 3}]
    assert body["genres"] == []


def test_repeated_query_params_reach_the_repository_as_lists(monkeypatch):
    seen = {}

    def capture(**kw):
        seen.update(kw)
        return []

    monkeypatch.setattr(db, "list_summaries", capture)
    r = client.get("/setlists?dj=ISOKNOCK&dj=Armin&genre=Techno&year=2025&q=edc")

    assert r.status_code == 200
    assert seen["dj"] == ["ISOKNOCK", "Armin"]
    assert seen["genre"] == ["Techno"] and seen["year"] == ["2025"]
    assert seen["q"] == "edc"


def test_summaries_expose_length_to_the_browse_cards(monkeypatch):
    monkeypatch.setattr(
        db, "list_summaries",
        lambda **kw: [{
            "id": "a", "title": "t", "dj_names": [], "event": None, "date_recorded": None,
            "genres": [], "media_url": None, "track_count": 1, "status": "resolved",
            "coverage": None, "created_at": None, "length_s": 3706,
        }],
    )
    assert client.get("/setlists").json()[0]["length_s"] == 3706
