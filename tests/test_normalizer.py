import json

import pytest

from soundseek.config import settings
from soundseek.models import ParsedTrack, RawSetlistPage, RawTrackRow
from soundseek.normalizer import (
    NormalizationError,
    _apply_played_with,
    _dump_llm_input,
    _validate_round_trip,
)


def _page(rows):
    return RawSetlistPage(source_url="https://example/tracklist/x/y.html", rows=rows)


def _row(position, raw_text, w=False):
    return RawTrackRow(position=position, raw_text=raw_text, is_played_with=w)


def _track(position, raw_text, **kw):
    return ParsedTrack(position=position, raw_text=raw_text, **kw)


class TestValidateRoundTrip:
    def test_accepts_exact_echo(self):
        page = _page([_row(1, "A - B")])
        _validate_round_trip(page, [_track(1, "A - B")])  # no raise

    def test_strips_echoed_input_framing(self):
        page = _page([_row(1, "A - B")])
        tracks = [_track(1, "1 | A - B")]
        _validate_round_trip(page, tracks)
        assert tracks[0].raw_text == "A - B"

    def test_rejects_wrong_count(self):
        page = _page([_row(1, "A - B"), _row(2, "C - D")])
        with pytest.raises(NormalizationError, match="2 input rows"):
            _validate_round_trip(page, [_track(1, "A - B")])

    def test_rejects_altered_text(self):
        page = _page([_row(1, "Fred again.. - Jungle")])
        with pytest.raises(NormalizationError, match="round-trip"):
            _validate_round_trip(page, [_track(1, "Fred Again - Jungle")])

    def test_rejects_reordered_positions(self):
        page = _page([_row(1, "A - B"), _row(2, "C - D")])
        with pytest.raises(NormalizationError):
            _validate_round_trip(page, [_track(2, "C - D"), _track(1, "A - B")])


class TestDumpLlmInput:
    def test_writes_debug_record(self, tmp_path):
        original = settings.data_dir
        settings.data_dir = tmp_path
        try:
            page = _page([_row(1, "A - B")])
            path = _dump_llm_input(page, "rows", "1 | A - B")
            record = json.loads(path.read_text(encoding="utf-8"))
        finally:
            settings.data_dir = original
        assert record["mode"] == "rows"
        assert record["llm_user_content"] == "1 | A - B"
        assert record["source_url"] == page.source_url
        assert record["extracted_page"]["rows"][0]["raw_text"] == "A - B"


class TestReattributeMashupCredit:
    """A credit closing a mashup row belongs to the row, not its last component.

    Models staple "[Someone Mashup]" onto whichever component ends the string;
    that component then matches nothing, because "San Holo - Lights
    (Flipboitamidles Mashup)" is not a track that exists anywhere.
    """

    def _mashup(self, raw_text, last_remix, first_remix=None, row_remix=None):
        from soundseek.models import MashupComponent
        from soundseek.normalizer import _reattribute_mashup_credit

        track = _track(1, raw_text, remix=row_remix, mashup_components=[
            MashupComponent(artists=["ZAXX"], title="Signal", remix=first_remix),
            MashupComponent(artists=["San Holo"], title="Lights", remix=last_remix),
        ])
        _reattribute_mashup_credit([track])
        return track

    def test_the_credit_moves_off_the_last_component(self):
        track = self._mashup(
            "ZAXX - Signal x San Holo - Lights [Flipboitamidles Mashup]",
            last_remix="Flipboitamidles Mashup",
        )
        assert track.mashup_components[1].remix is None
        assert track.remix == "Flipboitamidles Mashup"

    def test_trailing_row_separators_do_not_hide_it(self):
        """Pasted tracklists end rows with "/" — the credit is still the credit."""
        track = self._mashup(
            "ZAXX - Signal x San Holo - Lights [Flipboitamidles Mashup] / ",
            last_remix="Flipboitamidles Mashup",
        )
        assert track.mashup_components[1].remix is None

    def test_a_components_own_remix_is_left_alone(self):
        track = self._mashup(
            "ZAXX - Signal (Vicetone Remix) x San Holo - Lights [Nala Mashup]",
            first_remix="Vicetone Remix", last_remix="Nala Mashup",
        )
        assert track.mashup_components[0].remix == "Vicetone Remix"
        assert track.mashup_components[1].remix is None

    def test_a_parenthesised_remix_is_not_assumed_to_be_a_credit(self):
        """"(Vicetone Remix)" closing a row may genuinely be that component's."""
        track = self._mashup(
            "ZAXX - Signal x San Holo - Lights (Vicetone Remix)",
            last_remix="Vicetone Remix",
        )
        assert track.mashup_components[1].remix == "Vicetone Remix"
        assert track.remix is None

    def test_a_parenthesised_combination_credit_still_moves(self):
        track = self._mashup(
            "ZAXX - Signal x San Holo - Lights (Nala Bootleg)", last_remix="Nala Bootleg"
        )
        assert track.mashup_components[1].remix is None
        assert track.remix == "Nala Bootleg"

    def test_a_row_remix_already_set_is_not_overwritten(self):
        track = self._mashup(
            "ZAXX - Signal x San Holo - Lights [Nala Mashup]",
            last_remix="Nala Mashup", row_remix="Nala Mashup",
        )
        assert track.remix == "Nala Mashup"
        assert track.mashup_components[1].remix is None

    def test_it_does_nothing_when_the_model_got_it_right(self):
        track = self._mashup(
            "ZAXX - Signal x San Holo - Lights [Nala Mashup]",
            last_remix=None, row_remix="Nala Mashup",
        )
        assert track.mashup_components[1].remix is None
        assert track.remix == "Nala Mashup"

    def test_normal_tracks_keep_their_remix(self):
        from soundseek.normalizer import _reattribute_mashup_credit

        track = _track(1, "Pink - What About Us (Cash Cash Remix)", remix="Cash Cash Remix")
        _reattribute_mashup_credit([track])
        assert track.remix == "Cash Cash Remix"


class TestApplyPlayedWith:
    def test_w_rows_link_to_nearest_preceding_main_track(self):
        page = _page(
            [
                _row(1, "A - B"),
                _row(2, "C - D", w=True),
                _row(3, "E - F", w=True),
                _row(4, "G - H"),
                _row(5, "I - J", w=True),
            ]
        )
        tracks = [_track(r.position, r.raw_text, played_with=99) for r in page.rows]
        _apply_played_with(page, tracks)
        assert [t.played_with for t in tracks] == [None, 1, 1, None, 4]
