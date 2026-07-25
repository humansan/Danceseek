"""The local ingest runner: job lifecycle, platform choice, failure handling.

Every external edge (browser, LLM, Neon) is stubbed — this asserts the
orchestration, not the pipeline internals.
"""

import pytest

pytest.importorskip("fastapi")

from soundseek import ingest as runner  # noqa: E402
from soundseek import db, fetcher, pipeline, store  # noqa: E402
from soundseek.models import ParserInfo, Setlist, SetlistTrack  # noqa: E402
from soundseek.resolver.resolve import ResolveSummary  # noqa: E402

URL = "https://www.1001tracklists.com/tracklist/abc/dj-somewhere-2026.html"


def _setlist(id_="set-1") -> Setlist:
    return Setlist(
        id=id_, source_url=URL, title="DJ @ Somewhere", parser=ParserInfo(model="test"),
        tracks=[SetlistTrack(position=1, raw_text="A - T", artists=["A"], title="T")],
    )


@pytest.fixture
def stubbed(monkeypatch):
    """Stub the browser, the LLM normalize, and every db write."""
    calls = {"resolve": [], "coverage": [], "pages": [], "saved": []}

    monkeypatch.setattr(fetcher, "fetch_local", lambda url, force=False: "<html>page</html>")
    monkeypatch.setattr(db, "put_page", lambda url, html: (calls["pages"].append(url), 999)[1])
    monkeypatch.setattr(db, "get_by_url", lambda url: None)
    monkeypatch.setattr(db, "upsert_content", lambda s, status=None: None)
    monkeypatch.setattr(db, "set_status", lambda sid, st: None)
    monkeypatch.setattr(db, "set_coverage",
                        lambda sid, cov, status="resolved": calls["coverage"].append((sid, cov)))
    monkeypatch.setattr(store, "save", lambda s: calls["saved"].append(s.id))
    monkeypatch.setattr(pipeline, "build_from_html", lambda html, url: _setlist())

    def fake_resolve(setlist, force=False, platforms=None, on_progress=None, **kw):
        calls["resolve"].append({"platforms": platforms, "force": force})
        if on_progress:
            on_progress("matching", 1, 1)
        return ResolveSummary(resolved=1, platforms=list(platforms or []))

    monkeypatch.setattr(runner, "resolve_setlist", fake_resolve)
    monkeypatch.setattr(runner, "build_coverage", lambda s, summary: {"total": 1, "resolved": 1})
    return calls


# --- platform choice --------------------------------------------------------


def test_normalize_platforms_maps_the_checkbox():
    assert runner.normalize_platforms(lastfm_only=True) == ["lastfm"]
    assert runner.normalize_platforms(lastfm_only=False) == list(runner.ALL_PLATFORMS)
    assert runner.normalize_platforms(False, ["spotify"]) == ["spotify"]


def test_lastfm_only_job_resolves_against_lastfm_alone(stubbed):
    job = runner.Job(id="j", url=URL, platforms=["lastfm"], force=False)
    runner.run_job(job)
    assert job.status == "done"
    assert stubbed["resolve"][0]["platforms"] == ["lastfm"]


# --- the happy path ---------------------------------------------------------


def test_ingest_captures_stores_normalizes_and_resolves(stubbed):
    job = runner.Job(id="j", url=URL, platforms=list(runner.ALL_PLATFORMS), force=False)
    runner.run_job(job)

    assert job.status == "done"
    assert job.setlist_id == "set-1" and job.title == "DJ @ Somewhere"
    assert job.coverage == {"total": 1, "resolved": 1}
    assert stubbed["pages"] == [URL]  # the captured page is persisted
    assert stubbed["coverage"] == [("set-1", {"total": 1, "resolved": 1})]
    # The log narrates each stage for the console.
    assert any("fetching" in e["message"] for e in job.events)
    assert any("normalized 1 tracks" in e["message"] for e in job.events)


def test_progress_is_reported_for_the_ui(stubbed):
    job = runner.Job(id="j", url=URL, platforms=["lastfm"], force=False)
    runner.run_job(job)
    assert job.progress == (1, 1)
    assert any(e.get("progress") == [1, 1] for e in job.events)


# --- re-ingest guards -------------------------------------------------------


def test_known_url_is_left_alone_without_force(stubbed, monkeypatch):
    monkeypatch.setattr(db, "get_by_url", lambda url: (_setlist("existing"), {"status": "resolved"}))
    job = runner.Job(id="j", url=URL, platforms=["lastfm"], force=False)
    runner.run_job(job)

    assert job.status == "done" and job.setlist_id == "existing"
    assert stubbed["resolve"] == []  # nothing re-run
    assert any(e["level"] == "warn" and "already ingested" in e["message"] for e in job.events)


def test_force_reuses_the_existing_id_so_links_keep_working(stubbed, monkeypatch):
    monkeypatch.setattr(db, "get_by_url", lambda url: (_setlist("existing"), {"status": "resolved"}))
    job = runner.Job(id="j", url=URL, platforms=["lastfm"], force=True)
    runner.run_job(job)

    assert job.status == "done"
    assert job.setlist_id == "existing"  # not the fresh uuid from build_from_html
    assert stubbed["resolve"]


def test_reresolve_clears_only_the_unmatched_slots(stubbed, monkeypatch):
    from soundseek.models import Resolution

    setlist = _setlist("existing")
    setlist.tracks[0].resolution = Resolution(status="no_match")
    setlist.tracks.append(
        SetlistTrack(position=2, raw_text="B - U", artists=["B"], title="U",
                     resolution=Resolution(status="resolved"))
    )
    monkeypatch.setattr(db, "get_by_url", lambda url: (setlist, {"status": "resolved"}))

    job = runner.Job(id="j", url=URL, platforms=["lastfm"], force=False, mode="reresolve")
    runner.run_job(job)

    assert job.status == "done"
    assert setlist.tracks[0].resolution is None  # cleared for retry
    assert setlist.tracks[1].resolution is not None  # kept
    assert any("cleared 1 unmatched" in e["message"] for e in job.events)


# --- failures ---------------------------------------------------------------


def test_a_failed_job_records_the_error_instead_of_raising(stubbed, monkeypatch):
    def boom(url, force=False):
        raise fetcher.FetchError("Cloudflare did not clear")

    monkeypatch.setattr(fetcher, "fetch_local", boom)
    job = runner.Job(id="j", url=URL, platforms=["lastfm"], force=False)
    runner.run_job(job)  # must not raise

    assert job.status == "failed"
    assert "Cloudflare did not clear" in job.error
    assert any(e["level"] == "error" for e in job.events)


def test_reresolve_on_an_unknown_url_fails_clearly(stubbed):
    job = runner.Job(id="j", url=URL, platforms=["lastfm"], force=False, mode="reresolve")
    runner.run_job(job)
    assert job.status == "failed" and "not ingested yet" in job.error


# --- the job store ----------------------------------------------------------


def test_job_store_keeps_newest_first_and_bounded():
    store_ = runner.JobStore(keep=3)
    made = [store_.create(f"{URL}?{i}", ["lastfm"], False) for i in range(5)]

    recent = store_.recent()
    assert len(recent) == 3
    assert [j.id for j in recent] == [made[4].id, made[3].id, made[2].id]
    assert store_.get(made[0].id) is None  # evicted
    assert store_.get(made[4].id) is not None


def test_snapshot_is_json_safe():
    job = runner.Job(id="j", url=URL, platforms=["lastfm"], force=False)
    job.progress = (2, 7)
    snap = job.snapshot()
    assert snap["done"] == 2 and snap["total"] == 7
    assert snap["platforms"] == ["lastfm"] and snap["status"] == "queued"
