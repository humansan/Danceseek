from soundseek.exporter.collect import build_plan
from soundseek.models import (
    LastfmMatch,
    MashupComponent,
    ParserInfo,
    PlatformMatch,
    Resolution,
    Setlist,
    SetlistTrack,
)


def _match(id_):
    return PlatformMatch(id=id_, title="t", artists=["a"], url="u")


def _res(spotify=None, youtube=None, status="resolved"):
    return Resolution(
        status=status,
        spotify=_match(spotify) if spotify else None,
        youtube=_match(youtube) if youtube else None,
    )


def _setlist(tracks):
    return Setlist(source_url="https://x/tracklist/a/b.html", parser=ParserInfo(model="t"), tracks=tracks)


def _track(pos, raw, **kw):
    return SetlistTrack(position=pos, raw_text=raw, **kw)


class TestNormalTracks:
    def test_picks_target_id(self):
        sl = _setlist([_track(1, "A - B", resolution=_res(spotify="sp1", youtube="yt1"))])
        assert [i.id for i in build_plan(sl, "spotify").items] == ["sp1"]
        assert [i.id for i in build_plan(sl, "youtube").items] == ["yt1"]

    def test_no_match_skipped(self):
        sl = _setlist([_track(1, "A - B", resolution=_res(youtube="yt1"))])
        plan = build_plan(sl, "spotify")
        assert plan.items == []
        assert plan.skipped == [("A - B", "no_match")]

    def test_is_id_skipped_as_unreleased(self):
        sl = _setlist([_track(1, "ID - ID", is_id=True)])
        assert build_plan(sl, "spotify").skipped == [("ID - ID", "unreleased")]

    def test_null_resolution_is_no_match(self):
        sl = _setlist([_track(1, "A - B", resolution=None)])
        assert build_plan(sl, "youtube").skipped == [("A - B", "no_match")]


class TestPlayedWith:
    def test_included_by_default(self):
        sl = _setlist([_track(1, "A - B", played_with=0, resolution=_res(spotify="sp1"))])
        assert [i.id for i in build_plan(sl, "spotify").items] == ["sp1"]

    def test_skipped_when_requested(self):
        sl = _setlist([_track(1, "A - B", played_with=0, resolution=_res(spotify="sp1"))])
        plan = build_plan(sl, "spotify", skip_played_with=True)
        assert plan.items == []
        assert plan.skipped == [("A - B", "played_with_skipped")]


class TestMashups:
    def _mashup_track(self):
        return _track(
            1,
            "A vs. B - X vs. Y (Edit)",
            resolution=_res(youtube="mashupYT"),
            mashup_components=[
                MashupComponent(artists=["A"], title="X", resolution=_res(spotify="spX", youtube="ytX")),
                MashupComponent(artists=["B"], title="Y", resolution=_res(spotify="spY", youtube="ytY")),
            ],
        )

    def test_spotify_expands_to_components(self):
        sl = _setlist([self._mashup_track()])
        assert [i.id for i in build_plan(sl, "spotify").items] == ["spX", "spY"]

    def test_youtube_prefers_bootleg_row(self):
        sl = _setlist([self._mashup_track()])
        assert [i.id for i in build_plan(sl, "youtube").items] == ["mashupYT"]

    def test_youtube_falls_back_to_components_without_bootleg(self):
        t = self._mashup_track()
        t.resolution = _res()  # no youtube id on the mashup row
        sl = _setlist([t])
        assert [i.id for i in build_plan(sl, "youtube").items] == ["ytX", "ytY"]

    def test_no_expand_spotify_skips_mashup(self):
        sl = _setlist([self._mashup_track()])
        plan = build_plan(sl, "spotify", expand_mashups=False)
        assert plan.items == []
        assert plan.skipped == [("A vs. B - X vs. Y (Edit)", "no_match")]


class TestDedupeAndCoverage:
    def test_duplicate_ids_collapsed(self):
        sl = _setlist([
            _track(1, "A - B", resolution=_res(spotify="sp1")),
            _track(2, "A - B (reprise)", resolution=_res(spotify="sp1")),
            _track(3, "C - D", resolution=_res(spotify="sp2")),
        ])
        plan = build_plan(sl, "spotify")
        assert [i.id for i in plan.items] == ["sp1", "sp2"]
        assert ("A - B (reprise)", "duplicate") in plan.skipped
        assert plan.added == 2 and plan.total_considered == 3
