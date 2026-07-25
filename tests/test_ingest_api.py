"""The ingest console's HTTP layer: job creation, the SSE log stream, reads.

The pipeline itself is stubbed (see the `stubbed` fixture in
test_ingest_runner.py, re-used here) so no browser, LLM or database is touched.
"""

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from apps.ingest import main as console  # noqa: E402
from soundseek import db  # noqa: E402
from tests.test_ingest_runner import URL, stubbed  # noqa: E402,F401  (fixture re-use)

client = TestClient(console.app)


@pytest.fixture(autouse=True)
def fresh_jobs(monkeypatch):
    monkeypatch.setattr(console, "jobs", console.runner.JobStore())


def _wait_for_done(job_id: str) -> dict:
    """Jobs run on a thread; the SSE stream ends when the job settles."""
    with client.stream("GET", f"/api/jobs/{job_id}/events") as r:
        assert r.status_code == 200
        body = "".join(r.iter_text())
    return {"body": body, "job": client.get(f"/api/jobs/{job_id}").json()}


def test_the_console_page_is_served():
    r = client.get("/")
    assert r.status_code == 200
    assert "ingest console" in r.text
    assert 'id="lastfm_only"' in r.text  # the option is actually in the UI
    assert 'id="url"' in r.text


def test_creating_a_job_runs_the_pipeline(stubbed):  # noqa: F811
    r = client.post("/api/jobs", json={"url": URL, "lastfm_only": True})
    assert r.status_code == 201
    job = r.json()
    assert job["platforms"] == ["lastfm"]

    result = _wait_for_done(job["id"])
    assert result["job"]["status"] == "done"
    assert result["job"]["setlist_id"] == "set-1"
    assert stubbed["resolve"][0]["platforms"] == ["lastfm"]


def test_full_run_is_the_default_when_the_box_is_unchecked(stubbed):  # noqa: F811
    r = client.post("/api/jobs", json={"url": URL, "lastfm_only": False})
    assert r.json()["platforms"] == ["spotify", "youtube", "lastfm"]
    _wait_for_done(r.json()["id"])
    assert stubbed["resolve"][0]["platforms"] == ["spotify", "youtube", "lastfm"]


def test_the_event_stream_replays_the_log_and_the_final_status(stubbed):  # noqa: F811
    job_id = client.post("/api/jobs", json={"url": URL, "lastfm_only": True}).json()["id"]
    body = _wait_for_done(job_id)["body"]

    assert "event: log" in body and "event: status" in body
    assert "fetching" in body
    assert '"status": "done"' in body


def test_a_failing_job_streams_the_error(stubbed, monkeypatch):  # noqa: F811
    from soundseek import fetcher

    monkeypatch.setattr(fetcher, "fetch_local", lambda url, force=False: (_ for _ in ()).throw(
        fetcher.FetchError("challenge did not clear")
    ))
    job_id = client.post("/api/jobs", json={"url": URL}).json()["id"]
    result = _wait_for_done(job_id)

    assert result["job"]["status"] == "failed"
    assert "challenge did not clear" in result["body"]


def test_bad_url_is_rejected_before_any_work():
    r = client.post("/api/jobs", json={"url": "https://example.com/nope"})
    assert r.status_code == 422
    assert "1001tracklists" in r.json()["detail"]
    assert client.get("/api/jobs").json() == []


def test_bad_mode_is_rejected():
    r = client.post("/api/jobs", json={"url": URL, "mode": "delete-everything"})
    assert r.status_code == 422


def test_unknown_job_is_404():
    assert client.get("/api/jobs/nope").status_code == 404
    assert client.get("/api/jobs/nope/events").status_code == 404


def test_catalog_endpoint_reads_summaries(monkeypatch):
    monkeypatch.setattr(db, "list_summaries", lambda limit=30: [{"id": "a", "title": "T"}])
    assert client.get("/api/setlists").json() == [{"id": "a", "title": "T"}]


# --- the results table ------------------------------------------------------


def test_setlist_detail_feeds_the_results_table(monkeypatch):
    from tests.test_ingest_runner import _setlist

    meta = {"status": "resolved", "coverage": {"total": 1, "resolved": 1}, "resolved_at": None,
            "created_at": None}
    monkeypatch.setattr(db, "get_by_id", lambda sid: (_setlist(), meta))

    body = client.get("/api/setlists/set-1").json()
    assert body["status"] == "resolved"
    assert body["coverage"] == {"total": 1, "resolved": 1}
    # Every column the table renders must be present on a track.
    track = body["setlist"]["tracks"][0]
    for field in ("source_track_number", "position", "cue_time", "artists", "title",
                  "remix", "played_with", "is_id", "mashup_components", "resolution"):
        assert field in track


def test_unknown_setlist_detail_is_404(monkeypatch):
    monkeypatch.setattr(db, "get_by_id", lambda sid: None)
    assert client.get("/api/setlists/nope").status_code == 404


def test_the_page_contains_the_results_table_markup():
    page = client.get("/").text
    assert 'id="tracks"' in page and 'id="resultWrap"' in page
    for header in (">#<", ">row<", ">cue<", ">artists<", ">title<", ">remix<", ">flags<", ">res<"):
        assert header in page
