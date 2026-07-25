"""The manual path: a YouTube link plus a hand-pasted tracklist.

Used for sets 1001tracklists does not carry — the maintainer copies the video's
chapters or a comment tracklist. Timestamps and list numbering come off
deterministically here (they drive scrobble windows; a model should not be the
thing that gets them right), and only the artist/title/remix split goes to the
LLM — through the *same* prompt and round-trip validation the scraped path
uses. The LLM call is stubbed; what's asserted is the deterministic half and
that the result lands in the Setlist shape the scraped path produces.
"""

import pytest

from soundseek import ingest as runner
from soundseek import db, pipeline, store
from soundseek.extractor import ExtractionError, extract_manual
from soundseek.models import ParsedTrack, Setlist
from soundseek.pipeline import ManualInputError, build_from_manual, youtube_watch_url

VIDEO = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


# --- the URL is the record's key, so it has to canonicalize ------------------


class TestYoutubeUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtube.com/watch?v=dQw4w9WgXcQ&t=42s",
            "https://www.youtube.com/watch?list=PL123&v=dQw4w9WgXcQ",
            "https://youtu.be/dQw4w9WgXcQ",
            "https://youtu.be/dQw4w9WgXcQ?t=42",
            "https://www.youtube.com/live/dQw4w9WgXcQ",
            "https://www.youtube.com/embed/dQw4w9WgXcQ",
            "  https://www.youtube.com/shorts/dQw4w9WgXcQ  ",
        ],
    )
    def test_every_link_shape_lands_on_one_watch_url(self, url):
        """Same video pasted two ways must update one set, not create two."""
        assert youtube_watch_url(url) == VIDEO

    @pytest.mark.parametrize(
        "url", ["", "https://example.com/video", "https://vimeo.com/12345", "not a url"]
    )
    def test_non_youtube_links_are_rejected(self, url):
        with pytest.raises(ManualInputError):
            youtube_watch_url(url)


# --- reading the pasted lines (no LLM involved) ------------------------------


def _rows(text: str):
    page = extract_manual(text, VIDEO, "T")
    return [(r.position, r.cue_time, r.raw_text) for r in page.rows]


class TestExtractManual:
    def test_chapter_lines(self):
        assert _rows("0:00 Skrillex - Rumble\n3:41 Hamdi - Skanka (VIP)") == [
            (1, "0:00", "Skrillex - Rumble"),
            (2, "3:41", "Hamdi - Skanka (VIP)"),
        ]

    @pytest.mark.parametrize(
        "line, cue",
        [
            ("0:00 A - B", "0:00"),
            ("[0:00] A - B", "0:00"),
            ("(0:00) A - B", "0:00"),
            ("0:00 - A - B", "0:00"),
            ("0:00 — A - B", "0:00"),
            ("A - B @ 0:00", "0:00"),
            ("A - B [0:00]", "0:00"),
            ("1. 0:00 A - B", "0:00"),
        ],
    )
    def test_every_timestamp_placement_is_found(self, line, cue):
        assert _rows(line) == [(1, cue, "A - B")]

    @pytest.mark.parametrize(
        "written, stored",
        [("00:03:41", "3:41"), ("01:02:30", "1:02:30"), ("1:02:30", "1:02:30"),
         ("0:00:00", "0:00"), ("12:34", "12:34")],
    )
    def test_cue_times_are_normalized(self, written, stored):
        """These drive the scrobble windows, so the format has to be one thing."""
        assert _rows(f"{written} A - B")[0][1] == stored

    @pytest.mark.parametrize("line", ["1. A - B", "01) A - B", "12] A - B"])
    def test_list_numbering_is_stripped(self, line):
        assert _rows(line) == [(1, None, "A - B")]

    def test_lines_without_a_timestamp_still_count(self):
        assert _rows("A - B\nC - D") == [(1, None, "A - B"), (2, None, "C - D")]

    def test_obvious_junk_is_dropped(self):
        text = "Tracklist:\n\n0:00 A - B\nhttps://example.com/follow\n\n3:41 C - D\n---"
        assert _rows(text) == [(1, "0:00", "A - B"), (2, "3:41", "C - D")]

    def test_text_with_no_tracks_is_an_error(self):
        with pytest.raises(ExtractionError, match="No track lines"):
            extract_manual("\n\n  \nhttps://example.com\n", VIDEO, "T")

    def test_rows_carry_the_video_as_their_media(self):
        page = extract_manual("0:00 A - B", VIDEO, "T")
        assert page.media_url == VIDEO and page.media_kind == "youtube"


# --- assembling the setlist -------------------------------------------------


@pytest.fixture
def parsed(monkeypatch):
    """Stand in for the LLM: echoes each extracted row back, split on " - ".

    Deliberately a pass-through, so what these tests assert is the half of the
    manual path that is ours — the line parsing and the assembly.
    """
    seen = {}

    def fake(page):
        seen["page"] = page
        tracks = []
        for row in page.rows:
            artist, _, title = row.raw_text.partition(" - ")
            is_id = row.raw_text == "ID - ID"
            tracks.append(ParsedTrack(
                position=row.position, raw_text=row.raw_text,
                artists=[] if is_id else [artist],
                title=None if is_id else title, is_id=is_id,
            ))
        return tracks

    monkeypatch.setattr(pipeline, "normalize", fake)
    return seen


TEXT = "0:00 Skrillex - Rumble\n3:41 ID - ID"


class TestBuildFromManual:
    def test_it_produces_the_same_shape_the_scraped_path_does(self, parsed):
        setlist = build_from_manual(TEXT, VIDEO, "Skrillex @ Coachella 2025-04-12")

        assert isinstance(setlist, Setlist)
        assert setlist.source == "manual"
        # The video is both the source and the media the cue times refer to.
        assert setlist.source_url == VIDEO
        assert setlist.media_url == VIDEO and setlist.media_kind == "youtube"

    def test_cue_times_survive_onto_the_tracks(self, parsed):
        setlist = build_from_manual(TEXT, VIDEO, "T")
        assert [t.cue_time for t in setlist.tracks] == ["0:00", "3:41"]
        assert setlist.tracks[0].artists == ["Skrillex"]
        assert setlist.tracks[1].is_id is True

    def test_rows_are_numbered_for_display(self, parsed):
        setlist = build_from_manual(TEXT, VIDEO, "T")
        assert [t.source_track_number for t in setlist.tracks] == [1, 2]

    def test_the_llm_sees_rows_without_their_timestamps(self, parsed):
        """The prompt it reuses is the scraped one, which expects clean rows."""
        build_from_manual(TEXT, VIDEO, "T")
        assert [r.raw_text for r in parsed["page"].rows] == ["Skrillex - Rumble", "ID - ID"]

    def test_a_conventional_title_fills_the_browse_facets(self, parsed):
        """"DJ @ Event date" is the shape the scraper yields; hand-typed titles
        following it land on the same DJ/event/year filter chips."""
        setlist = build_from_manual(TEXT, VIDEO, "Skrillex & Fred again.. @ Coachella 2025-04-12")
        assert setlist.dj_names == ["Skrillex", "Fred again.."]
        assert setlist.event == "Coachella" and setlist.date_recorded == "2025-04-12"

    def test_a_freeform_title_is_kept_as_is(self, parsed):
        setlist = build_from_manual(TEXT, VIDEO, "Some Random Mix")
        assert setlist.title == "Some Random Mix" and setlist.dj_names == []

    def test_the_link_is_canonicalized_before_anything_else(self, parsed):
        setlist = build_from_manual(TEXT, "https://youtu.be/dQw4w9WgXcQ?t=9", "T")
        assert setlist.source_url == VIDEO

    def test_a_bad_link_fails_before_the_llm_is_called(self, parsed):
        with pytest.raises(ManualInputError):
            build_from_manual(TEXT, "https://example.com/x", "T")
        assert parsed == {}  # no LLM spend on input that was never usable


# --- the job -----------------------------------------------------------------


@pytest.fixture
def manual_job(monkeypatch, parsed):
    """Stub every write edge; the manual path must not touch the browser at all."""
    calls = {"resolve": [], "coverage": [], "saved": [], "pages": []}

    monkeypatch.setattr(db, "get_by_url", lambda url: None)
    monkeypatch.setattr(db, "upsert_content", lambda s, status=None: None)
    monkeypatch.setattr(db, "set_coverage",
                        lambda sid, cov, status="resolved": calls["coverage"].append((sid, cov)))
    monkeypatch.setattr(db, "put_page", lambda url, html: calls["pages"].append(url))
    monkeypatch.setattr(store, "save", lambda s: calls["saved"].append(s.id))

    from soundseek.resolver.resolve import ResolveSummary

    def fake_resolve(setlist, force=False, platforms=None, on_progress=None, **kw):
        calls["resolve"].append({"platforms": platforms, "tracks": len(setlist.tracks)})
        return ResolveSummary(resolved=len(setlist.tracks), platforms=list(platforms or []))

    monkeypatch.setattr(runner, "resolve_setlist", fake_resolve)
    monkeypatch.setattr(runner, "build_coverage", lambda s, summary: {"total": 2, "resolved": 2})
    return calls


def _job(url=VIDEO, **kw) -> runner.Job:
    kw.setdefault("manual_text", "0:00 Skrillex - Rumble\n3:41 ID - ID")
    kw.setdefault("manual_title", "Skrillex @ Coachella 2025-04-12")
    kw.setdefault("force", False)
    return runner.Job(id="j", url=url, platforms=["lastfm"], mode="manual", **kw)


class TestManualJob:
    def test_it_normalizes_then_resolves(self, manual_job):
        job = _job()
        runner.run_job(job)

        assert job.status == "done"
        assert job.title == "Skrillex @ Coachella 2025-04-12"
        assert manual_job["resolve"] == [{"platforms": ["lastfm"], "tracks": 2}]
        assert manual_job["coverage"] == [(job.setlist_id, {"total": 2, "resolved": 2})]
        assert any("parsed 2 tracks" in e["message"] for e in job.events)

    def test_nothing_is_scraped_or_captured(self, manual_job):
        """There is no page — the pasted text is the source."""
        runner.run_job(_job())
        assert manual_job["pages"] == []

    def test_the_job_url_is_canonicalized(self, manual_job):
        job = _job(url="https://youtu.be/dQw4w9WgXcQ?t=9")
        runner.run_job(job)
        assert job.url == VIDEO

    def test_a_known_video_is_left_alone_without_force(self, manual_job, monkeypatch):
        existing = Setlist(id="existing", source_url=VIDEO, source="manual",
                           parser={"model": "test"}, tracks=[])
        monkeypatch.setattr(db, "get_by_url", lambda url: (existing, {"status": "resolved"}))

        job = _job()
        runner.run_job(job)
        assert job.status == "done" and job.setlist_id == "existing"
        assert manual_job["resolve"] == []
        assert any("already added" in e["message"] for e in job.events)

    def test_force_replaces_it_under_the_same_id(self, manual_job, monkeypatch):
        existing = Setlist(id="existing", source_url=VIDEO, source="manual",
                           parser={"model": "test"}, tracks=[])
        monkeypatch.setattr(db, "get_by_url", lambda url: (existing, {"status": "resolved"}))

        job = _job(force=True)
        runner.run_job(job)
        assert job.setlist_id == "existing"  # links to the set keep working
        assert manual_job["resolve"]

    def test_a_bad_link_fails_the_job_instead_of_raising(self, manual_job):
        job = _job(url="https://example.com/nope")
        runner.run_job(job)
        assert job.status == "failed" and "Not a YouTube video URL" in job.error
