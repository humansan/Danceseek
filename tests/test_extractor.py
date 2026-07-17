from pathlib import Path

import pytest

from soundseek.extractor import ExtractionError, extract

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def toronto_page():
    return extract(_load("toronto.html"), "https://example/tracklist/n4387rk/x.html")


@pytest.fixture(scope="module")
def twitch_page():
    return extract(_load("twitch.html"), "https://example/tracklist/2mgq6czt/x.html")


class TestToronto:
    @pytest.fixture
    def page(self, toronto_page):
        return toronto_page

    def test_metadata(self, page):
        assert page.title == "Fred again.. @ USB002, YZD Hanger 5, Toronto, Canada 2025-11-14"
        assert page.dj_names == ["Fred again.."]
        assert page.event == "USB002, YZD Hanger 5, Toronto, Canada"
        assert page.date_recorded == "2025-11-14"
        assert "House" in page.genres

    def test_rows(self, page):
        assert len(page.rows) == 34
        assert page.rows[0].raw_text == "Fred again.. & Young Thug - scared"
        assert page.rows[0].cue_time == "0:00"
        assert page.rows[0].position == 1
        # positions are contiguous and ordered
        assert [r.position for r in page.rows] == list(range(1, 35))

    def test_source_track_numbers(self, page):
        # 34 rows = 29 numbered tracks + 5 "w/" rows; site numbering skips w/
        numbered = [r.source_track_number for r in page.rows if not r.is_played_with]
        assert numbered == list(range(1, 30))
        assert all(r.source_track_number is None for r in page.rows if r.is_played_with)
        # positions diverge from site numbers once a w/ row has appeared
        last = page.rows[-1]
        assert (last.position, last.source_track_number) == (34, 29)

    def test_played_with_rows(self, page):
        w_rows = [r for r in page.rows if r.is_played_with]
        assert len(w_rows) == 5
        # w/ rows can carry real cue times (needed for scrobbling)...
        assert w_rows[0].cue_time == "5:30"
        assert w_rows[1].cue_time == "7:40"
        # ...but the site's placeholder 0 must still come back as None
        assert w_rows[2].cue_time is None

    def test_id_row_kept_verbatim(self, page):
        assert page.rows[-1].raw_text == "Fred again.. - ID"

    def test_linked_media_video(self, page):
        assert page.media_url == "https://www.youtube.com/watch?v=s4ddlQMTxDQ"
        assert page.media_kind == "youtube"


class TestTwitch:
    @pytest.fixture
    def page(self, twitch_page):
        return twitch_page

    def test_multi_dj_split(self, page):
        assert page.dj_names == ["Fred again..", "Sammy Virji"]

    def test_mashup_row_verbatim(self, page):
        mashup = page.rows[3]
        assert (
            mashup.raw_text
            == "Fred again.. & Skepta vs. Yo Speed - Last 1s Left vs. Muita (Fred again.. Mashup)"
        )

    def test_cue_zero_only_valid_on_first_track(self, page):
        # site stores unknown cues as 0; those must come back as None
        assert all(r.cue_time != "0:00" for r in page.rows if r.position != 1)

    def test_linked_media_video(self, page):
        assert page.media_url == "https://www.youtube.com/watch?v=uPSGBvGPw7M"
        assert page.media_kind == "youtube"


@pytest.fixture(scope="module")
def edc_page():
    return extract(_load("edc.html"), "https://example/tracklist/edc/x.html")


class TestEdcMashupComponents:
    @pytest.fixture
    def page(self, edc_page):
        return edc_page

    def test_component_subrows_folded_into_parent(self, page):
        mashup = next(r for r in page.rows if "She A Freak Edit" in r.raw_text)
        assert mashup.component_texts == [
            "Empire Of The Sun - Walking On A Dream",
            "Armin van Buuren & Skytech - She A Freak",
        ]

    def test_components_are_not_independent_rows(self, page):
        texts = [r.raw_text for r in page.rows]
        assert "Empire Of The Sun - Walking On A Dream" not in texts
        assert "Armin van Buuren & Skytech - She A Freak" not in texts

    def test_positions_still_contiguous(self, page):
        assert [r.position for r in page.rows] == list(range(1, len(page.rows) + 1))

    def test_numbered_rows_match_site_numbering(self, page):
        # sub-rows don't consume site numbers; numbering stays aligned
        bounty = next(r for r in page.rows if r.raw_text.startswith("Bountyhunter"))
        assert bounty.source_track_number == 4

    def test_linked_media_video(self, page):
        # VideoObject embed normalized to a watch URL
        assert page.media_url == "https://www.youtube.com/watch?v=Vh8y7ro0mrQ"
        assert page.media_kind == "youtube"


def test_non_tracklist_page_raises():
    with pytest.raises(ExtractionError):
        extract("<html><body><p>hello</p></body></html>", "https://example/tracklist/x/y.html")


def test_prose_page_falls_back_to_text():
    body = "<p>tracklist " + "Artist - Title. " * 50 + "</p>"
    page = extract(f"<html><body>{body}</body></html>", "https://example/tracklist/x/y.html")
    assert page.rows == []
    assert page.fallback_text and "Artist - Title" in page.fallback_text
    # no player markup -> media stays null
    assert page.media_url is None and page.media_kind is None
