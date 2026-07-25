"""Maintainer edits: what a correction does to the resolutions underneath it.

The interesting cases are all about not scrobbling the wrong thing — a match
picked for text the maintainer has since changed is worse than no match at all.
"""

import pytest

from soundseek.edit import (
    ComponentEdit,
    SetlistEdit,
    TrackEdit,
    apply_edits,
    coverage_for,
)
from soundseek.models import (
    LastfmMatch,
    MashupComponent,
    ParserInfo,
    PlatformMatch,
    Resolution,
    Setlist,
    SetlistTrack,
)

URL = "https://www.1001tracklists.com/tracklist/abc/dj-somewhere-2026.html"


def _lastfm(artist="Skrillex", track="Rumble"):
    return Resolution(status="resolved", confidence=0.9, lastfm=LastfmMatch(artist=artist, track=track))


def _setlist(*tracks) -> Setlist:
    return Setlist(
        id="set-1", source_url=URL, title="DJ @ Somewhere",
        parser=ParserInfo(model="test"), tracks=list(tracks),
    )


def _track(position=1, **kw) -> SetlistTrack:
    kw.setdefault("raw_text", "Skrillex - Rumble")
    kw.setdefault("artists", ["Skrillex"])
    kw.setdefault("title", "Rumble")
    return SetlistTrack(position=position, **kw)


def _edit(position=1, **kw) -> SetlistEdit:
    kw.setdefault("artists", ["Skrillex"])
    kw.setdefault("title", "Rumble")
    return SetlistEdit(tracks=[TrackEdit(position=position, **kw)])


def _echo(track: SetlistTrack, **kw) -> SetlistEdit:
    """An edit as the console submits it: the stored Last.fm target echoed back.

    That echo is what makes "unchanged" distinguishable from "cleared" — see
    the module docstring in soundseek.edit.
    """
    match = track.resolution.lastfm if track.resolution else None
    kw.setdefault("lastfm_artist", match.artist if match else None)
    kw.setdefault("lastfm_track", match.track if match else None)
    return _edit(track.position, **kw)


# --- plain field edits ------------------------------------------------------


class TestFields:
    def test_edits_land_on_the_track(self):
        setlist = _setlist(_track(resolution=_lastfm()))
        apply_edits(setlist, _edit(artists=["Fred again..", "Skrillex"], title="Rumble",
                                   remix="Extended Mix", cue_time="1:02:30"))
        track = setlist.tracks[0]
        assert track.artists == ["Fred again..", "Skrillex"]
        assert track.remix == "Extended Mix"
        assert track.cue_time == "1:02:30"

    def test_raw_text_is_never_edited_away(self):
        """It is the source's own words — the provenance everything re-derives from."""
        setlist = _setlist(_track())
        apply_edits(setlist, _edit(artists=["Someone Else"], title="Different"))
        assert setlist.tracks[0].raw_text == "Skrillex - Rumble"

    def test_blank_fields_become_null_not_empty_strings(self):
        setlist = _setlist(_track(remix="VIP", cue_time="0:10"))
        apply_edits(setlist, _edit(remix="  ", cue_time=""))
        assert setlist.tracks[0].remix is None
        assert setlist.tracks[0].cue_time is None

    def test_blank_artist_entries_are_dropped(self):
        setlist = _setlist(_track())
        apply_edits(setlist, _edit(artists=["Hamdi", "  ", ""]))
        assert setlist.tracks[0].artists == ["Hamdi"]

    def test_the_title_can_be_renamed(self):
        setlist = _setlist(_track())
        apply_edits(setlist, SetlistEdit(title="New Name @ Elsewhere", tracks=[
            TrackEdit(position=1, artists=["Skrillex"], title="Rumble")]))
        assert setlist.title == "New Name @ Elsewhere"


# --- what an edit does to the resolution ------------------------------------


class TestResolutionInvalidation:
    def test_changing_the_identity_drops_the_match(self):
        track = _track(resolution=_lastfm())
        setlist = _setlist(track)
        apply_edits(setlist, _echo(track, title="Rumble (VIP)"))
        assert setlist.tracks[0].resolution is None

    def test_an_untouched_row_keeps_its_match(self):
        track = _track(resolution=_lastfm())
        setlist = _setlist(track)
        apply_edits(setlist, _echo(track))
        assert setlist.tracks[0].resolution.lastfm.track == "Rumble"

    def test_a_cue_fix_alone_keeps_the_match(self):
        track = _track(cue_time="0:00", resolution=_lastfm())
        setlist = _setlist(track)
        apply_edits(setlist, _echo(track, cue_time="0:12"))
        assert setlist.tracks[0].resolution is not None

    def test_marking_a_row_id_makes_it_unreleased(self):
        track = _track(resolution=_lastfm())
        setlist = _setlist(track)
        apply_edits(setlist, _echo(track, is_id=True))
        res = setlist.tracks[0].resolution
        assert res.status == "unreleased" and res.lastfm is None

    def test_unmarking_id_clears_the_slot_for_the_matcher(self):
        setlist = _setlist(_track(is_id=True, resolution=Resolution(status="unreleased", method="skip")))
        apply_edits(setlist, _edit(is_id=False))
        assert setlist.tracks[0].resolution is None


class TestManualLastfmTarget:
    def test_typing_a_target_stamps_a_manual_resolution(self):
        setlist = _setlist(_track(resolution=_lastfm()))
        apply_edits(setlist, _edit(lastfm_artist="Skrillex", lastfm_track="Rumble - VIP"))
        res = setlist.tracks[0].resolution
        assert res.method == "manual" and res.confidence == 1.0
        assert (res.lastfm.artist, res.lastfm.track) == ("Skrillex", "Rumble - VIP")

    def test_a_manual_target_survives_an_identity_edit(self):
        """The hand-typed strings are the most authoritative thing in the record."""
        setlist = _setlist(_track(resolution=_lastfm()))
        apply_edits(setlist, _edit(title="Rumble", lastfm_artist="A", lastfm_track="B"))
        apply_edits(setlist, _edit(title="Completely Different", lastfm_artist="A", lastfm_track="B"))
        res = setlist.tracks[0].resolution
        assert res.method == "manual" and res.lastfm.track == "B"

    def test_half_a_target_is_not_a_target(self):
        setlist = _setlist(_track(resolution=_lastfm()))
        apply_edits(setlist, _edit(lastfm_artist="Skrillex", lastfm_track=""))
        assert setlist.tracks[0].resolution.lastfm is None  # read as "clear it"

    def test_clearing_it_keeps_the_other_platforms(self):
        res = _lastfm()
        res.spotify = PlatformMatch(id="s1", title="Rumble", url="https://open.spotify/x")
        setlist = _setlist(_track(resolution=res))
        apply_edits(setlist, _edit(lastfm_artist=None, lastfm_track=None))

        after = setlist.tracks[0].resolution
        assert after.lastfm is None and after.spotify is not None
        assert after.status == "partial"

    def test_clearing_the_only_match_is_no_match(self):
        setlist = _setlist(_track(resolution=_lastfm()))
        apply_edits(setlist, _edit(lastfm_artist=None, lastfm_track=None))
        assert setlist.tracks[0].resolution.status == "no_match"


# --- adding, deleting, reordering -------------------------------------------


class TestRowLifecycle:
    def test_a_row_left_out_is_deleted(self):
        setlist = _setlist(_track(1), _track(2, raw_text="B - U", artists=["B"], title="U"))
        apply_edits(setlist, _edit(position=2, artists=["B"], title="U"))
        assert [t.title for t in setlist.tracks] == ["U"]

    def test_a_null_position_adds_a_row(self):
        setlist = _setlist(_track(1))
        apply_edits(setlist, SetlistEdit(tracks=[
            TrackEdit(position=1, artists=["Skrillex"], title="Rumble"),
            TrackEdit(position=None, artists=["Hamdi"], title="Skanka", remix="VIP",
                      cue_time="4:20"),
        ]))
        added = setlist.tracks[1]
        assert added.title == "Skanka" and added.cue_time == "4:20"
        assert added.raw_text == "Hamdi - Skanka (VIP)"  # synthesized: there is no source
        assert added.resolution is None  # nothing has matched it yet

    def test_positions_are_renumbered_densely(self):
        setlist = _setlist(_track(1), _track(2, title="U"), _track(3, title="V"))
        apply_edits(setlist, SetlistEdit(tracks=[
            TrackEdit(position=3, artists=["Skrillex"], title="V"),
            TrackEdit(position=1, artists=["Skrillex"], title="Rumble"),
        ]))
        assert [(t.position, t.title) for t in setlist.tracks] == [(1, "V"), (2, "Rumble")]

    def test_played_with_follows_its_row(self):
        setlist = _setlist(
            _track(1), _track(2, title="U"), _track(3, title="V", played_with=1)
        )
        apply_edits(setlist, SetlistEdit(tracks=[
            TrackEdit(position=1, artists=["Skrillex"], title="Rumble"),
            TrackEdit(position=3, artists=["Skrillex"], title="V"),
        ]))
        assert setlist.tracks[1].played_with == 1  # still points at "Rumble"

    def test_played_with_a_deleted_row_becomes_null(self):
        setlist = _setlist(_track(1), _track(2, title="U", played_with=1))
        apply_edits(setlist, _edit(position=2, artists=["Skrillex"], title="U"))
        assert setlist.tracks[0].played_with is None

    def test_an_empty_edit_is_rejected(self):
        setlist = _setlist(_track(1))
        with pytest.raises(ValueError, match="at least one track"):
            apply_edits(setlist, SetlistEdit(tracks=[]))

    def test_a_duplicated_position_is_rejected(self):
        setlist = _setlist(_track(1))
        with pytest.raises(ValueError, match="twice"):
            apply_edits(setlist, SetlistEdit(tracks=[
                TrackEdit(position=1, title="A"), TrackEdit(position=1, title="B")]))


# --- mashup components ------------------------------------------------------


class TestComponents:
    def test_components_are_edited_by_order(self):
        track = _track(resolution=_lastfm(), mashup_components=[
            MashupComponent(artists=["A"], title="One", resolution=_lastfm("A", "One")),
            MashupComponent(artists=["B"], title="Two", resolution=_lastfm("B", "Two")),
        ])
        setlist = _setlist(track)
        apply_edits(setlist, SetlistEdit(tracks=[TrackEdit(
            position=1, artists=["Skrillex"], title="Rumble",
            mashup_components=[
                ComponentEdit(artists=["A"], title="One",
                              lastfm_artist="A", lastfm_track="One"),
                ComponentEdit(artists=["B"], title="Two, Revisited",
                              lastfm_artist="B", lastfm_track="Two"),
            ],
        )]))
        components = setlist.tracks[0].mashup_components
        assert components[0].resolution is not None  # untouched
        assert components[1].resolution is None  # retitled -> stale match dropped

    def test_a_component_takes_a_manual_target_too(self):
        track = _track(mashup_components=[MashupComponent(artists=["A"], title="One")])
        setlist = _setlist(track)
        apply_edits(setlist, SetlistEdit(tracks=[TrackEdit(
            position=1, artists=["Skrillex"], title="Rumble",
            mashup_components=[ComponentEdit(artists=["A"], title="One",
                                             lastfm_artist="A", lastfm_track="One")],
        )]))
        res = setlist.tracks[0].mashup_components[0].resolution
        assert res.method == "manual" and res.lastfm.artist == "A"


# --- coverage ---------------------------------------------------------------


class TestCoverage:
    def test_counts_come_from_the_stored_resolutions(self):
        setlist = _setlist(
            _track(1, resolution=_lastfm()),
            _track(2, title="U", resolution=Resolution(status="no_match")),
            _track(3, title="V", is_id=True, resolution=Resolution(status="unreleased")),
        )
        coverage = coverage_for(setlist, ["lastfm"])
        assert coverage["resolved"] == 1 and coverage["no_match"] == 1
        assert coverage["unreleased"] == 1 and coverage["total"] == 3
        assert coverage["lastfm"] == 1 and coverage["platforms"] == ["lastfm"]

    def test_slots_an_edit_invalidated_are_reported_as_pending(self):
        setlist = _setlist(_track(1, resolution=_lastfm()), _track(2, title="U"))
        assert coverage_for(setlist, ["lastfm"])["pending"] == 1

    def test_components_are_counted_alongside_their_row(self):
        setlist = _setlist(_track(1, resolution=Resolution(status="no_match"),
                                  mashup_components=[
                                      MashupComponent(artists=["A"], title="One",
                                                      resolution=_lastfm("A", "One"))]))
        coverage = coverage_for(setlist, ["lastfm"])
        assert coverage["total"] == 2 and coverage["lastfm"] == 1
