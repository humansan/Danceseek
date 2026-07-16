import pytest

from soundseek.models import ParsedTrack, RawSetlistPage, RawTrackRow
from soundseek.normalizer import NormalizationError, _apply_played_with, _validate_round_trip


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
