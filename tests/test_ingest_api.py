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


def test_the_page_offers_editing_and_the_manual_source():
    page = client.get("/").text
    for control in ('id="editBtn"', 'id="saveBtn"', 'id="cancelBtn"', 'id="addRow"'):
        assert control in page
    for field in ('id="ytUrl"', 'id="manualTitle"', 'id="manualText"'):
        assert field in page


# --- editing the results ----------------------------------------------------


@pytest.fixture
def editable(monkeypatch):
    """A one-track set in the 'database', with the writes captured."""
    from soundseek import edit as edit_module
    from tests.test_ingest_runner import _setlist

    saved: dict = {}
    setlist = _setlist()
    meta = {"status": "resolved", "coverage": {"platforms": ["lastfm"]},
            "resolved_at": None, "created_at": None}

    monkeypatch.setattr(db, "get_by_id", lambda sid: (setlist, meta) if sid == "set-1" else None)

    def fake_save(edited, m):
        saved["setlist"] = edited
        saved["status"] = m.get("status")
        return edit_module.coverage_for(edited, (m.get("coverage") or {}).get("platforms"))

    monkeypatch.setattr(console.edit, "save_edited", fake_save)
    return saved


def _body(**kw) -> dict:
    kw.setdefault("position", 1)
    kw.setdefault("artists", ["A"])
    kw.setdefault("title", "T")
    return {"tracks": [kw]}


def test_editing_a_track_persists_the_new_values(editable):
    r = client.patch("/api/setlists/set-1", json=_body(title="T (VIP)", cue_time="0:42"))
    assert r.status_code == 200

    track = r.json()["setlist"]["tracks"][0]
    assert track["title"] == "T (VIP)" and track["cue_time"] == "0:42"
    assert editable["setlist"].tracks[0].title == "T (VIP)"


def test_the_response_carries_the_recomputed_coverage(editable):
    r = client.patch("/api/setlists/set-1", json=_body())
    coverage = r.json()["coverage"]
    assert coverage["platforms"] == ["lastfm"]
    assert coverage["pending"] == 1  # the stub track was never resolved


def test_an_edit_does_not_change_the_lifecycle_status(editable):
    """A corrected cue time must not lock the set out of exporting."""
    r = client.patch("/api/setlists/set-1", json=_body())
    assert r.json()["status"] == "resolved"
    assert editable["status"] == "resolved"


def test_a_row_left_out_of_the_submission_is_deleted(editable):
    from soundseek.models import SetlistTrack

    editable_setlist = db.get_by_id("set-1")[0]
    editable_setlist.tracks.append(SetlistTrack(position=2, raw_text="B - U", title="U"))

    r = client.patch("/api/setlists/set-1", json=_body(position=2, title="U"))
    assert [t["title"] for t in r.json()["setlist"]["tracks"]] == ["U"]


def test_an_empty_edit_is_rejected(editable):
    r = client.patch("/api/setlists/set-1", json={"tracks": []})
    assert r.status_code == 422
    assert "at least one track" in r.json()["detail"]


def test_editing_an_unknown_setlist_is_404(editable):
    assert client.patch("/api/setlists/nope", json=_body()).status_code == 404


# --- manual sets ------------------------------------------------------------


@pytest.fixture
def manual(monkeypatch):
    """Stub the manual pipeline the same way `stubbed` does the scraped one."""
    from soundseek.resolver.resolve import ResolveSummary
    from tests.test_ingest_runner import _setlist

    calls = {"built": []}
    setlist = _setlist("manual-1")

    def fake_build(text, url, title=None):
        calls["built"].append({"text": text, "url": url, "title": title})
        return setlist

    monkeypatch.setattr(console.runner.pipeline, "build_from_manual", fake_build)
    monkeypatch.setattr(db, "get_by_url", lambda url: None)
    monkeypatch.setattr(db, "upsert_content", lambda s, status=None: None)
    monkeypatch.setattr(db, "set_coverage", lambda sid, cov, status="resolved": None)
    monkeypatch.setattr(console.runner.store, "save", lambda s: None)
    monkeypatch.setattr(console.runner, "resolve_setlist",
                        lambda s, **kw: ResolveSummary(resolved=1, platforms=["lastfm"]))
    monkeypatch.setattr(console.runner, "build_coverage", lambda s, summary: {"total": 1})
    return calls


VIDEO = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_a_manual_job_takes_the_link_title_and_text(manual):
    r = client.post("/api/jobs", json={
        "url": "https://youtu.be/dQw4w9WgXcQ?t=9", "mode": "manual",
        "title": "DJ @ Fest 2026-01-01", "tracklist": "0:00 A - T",
        "lastfm_only": True,
    })
    assert r.status_code == 201
    assert r.json()["url"] == VIDEO  # canonicalized before the job is created

    result = _wait_for_done(r.json()["id"])
    assert result["job"]["status"] == "done"
    assert manual["built"] == [
        {"text": "0:00 A - T", "url": VIDEO, "title": "DJ @ Fest 2026-01-01"}
    ]


def test_a_manual_job_needs_tracklist_text():
    r = client.post("/api/jobs", json={"url": VIDEO, "mode": "manual", "tracklist": "   "})
    assert r.status_code == 422
    assert "tracklist text" in r.json()["detail"]
    assert client.get("/api/jobs").json() == []


def test_a_manual_job_needs_a_youtube_link():
    r = client.post("/api/jobs", json={
        "url": "https://example.com/video", "mode": "manual", "tracklist": "0:00 A - T"})
    assert r.status_code == 422
    assert "YouTube" in r.json()["detail"]


def test_a_1001tracklists_url_is_still_rejected_for_the_scraped_mode():
    r = client.post("/api/jobs", json={"url": VIDEO, "mode": "ingest"})
    assert r.status_code == 422
    assert "1001tracklists" in r.json()["detail"]
