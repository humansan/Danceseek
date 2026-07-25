"""Cue windows — the shared truth behind the highlight and the scrobbler."""

import pytest

from soundseek.models import LastfmMatch, ParserInfo, Resolution, Setlist, SetlistTrack
from soundseek.scrobble.windows import (
    MIN_WINDOW_S,
    ScrobbleConfig,
    build_windows,
    cue_seconds,
)


def _track(position, cue=None, artists=("A",), title="T", **kw) -> SetlistTrack:
    return SetlistTrack(
        position=position, cue_time=cue, raw_text=f"{'/'.join(artists)} - {title}",
        artists=list(artists), title=title, **kw,
    )


def _setlist(*tracks) -> Setlist:
    return Setlist(
        id="set-1", source_url="https://example/tracklist/a/b.html",
        parser=ParserInfo(model="test"), tracks=list(tracks),
    )


# --- parsing ----------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [("0:00", 0), ("0:12", 12), ("5:27", 327), ("1:02:30", 3750), (" 1:16:33 ", 4593)],
)
def test_cue_seconds_parses_both_shapes(text, expected):
    assert cue_seconds(text) == expected


@pytest.mark.parametrize("text", [None, "", "soon", "12", "1:2:3:4", "-1:00", "a:bb"])
def test_cue_seconds_rejects_junk(text):
    assert cue_seconds(text) is None


# --- windows from real cues -------------------------------------------------


def test_windows_run_from_each_cue_to_the_next():
    ws = build_windows(_setlist(_track(1, "0:00"), _track(2, "5:00"), _track(3, "9:00")), 900)
    assert ws.timing == "cue" and ws.live_capable
    assert [(w.start_s, w.end_s) for w in ws.windows] == [(0, 300), (300, 540), (540, 900)]


def test_the_last_window_ends_at_the_recording_duration():
    ws = build_windows(_setlist(_track(1, "0:00"), _track(2, "5:00")), media_duration_s=3600)
    assert ws.windows[-1].end_s == 3600


def test_without_a_duration_the_last_window_gets_a_sane_tail():
    ws = build_windows(_setlist(_track(1, "0:00"), _track(2, "5:00")))
    assert ws.windows[-1].end_s == 300 + 480


def test_a_duration_shorter_than_the_last_cue_is_ignored():
    """A bad duration must never produce a backwards window."""
    ws = build_windows(_setlist(_track(1, "0:00"), _track(2, "50:00")), media_duration_s=60)
    last = ws.windows[-1]
    assert last.end_s > last.start_s


def test_layered_rows_without_a_cue_share_the_window_they_sit_in():
    """A `w/` row is layered over the row above and often carries no cue of its
    own — it must still be present and scrobblable, not dropped."""
    ws = build_windows(
        _setlist(
            _track(1, "0:00"),
            _track(2, None, played_with=1),  # w/ row, no cue
            _track(3, "5:00"),
        ),
        900,
    )
    assert (ws.windows[1].start_s, ws.windows[1].end_s) == (0, 300)
    assert ws.windows[1].position == 2


def test_out_of_order_cues_are_dropped_rather_than_inverted():
    ws = build_windows(_setlist(_track(1, "0:00"), _track(2, "9:00"), _track(3, "5:00")), 900)
    assert all(w.end_s >= w.start_s for w in ws.windows)


# --- estimated (no cues at all) ---------------------------------------------


def test_a_set_with_no_cues_still_gets_windows_marked_estimated():
    ws = build_windows(_setlist(_track(1), _track(2), _track(3)), media_duration_s=900)
    assert ws.timing == "estimated"
    assert ws.live_capable is False  # can't know what's playing without cues
    assert [(w.start_s, w.end_s) for w in ws.windows] == [(0, 300), (300, 600), (600, 900)]
    assert all(w.timing == "estimated" for w in ws.windows)


def test_estimated_without_a_duration_assumes_a_track_length():
    ws = build_windows(_setlist(_track(1), _track(2)))
    assert ws.windows[0].end_s == 300 and ws.windows[-1].end_s == 600


def test_estimated_sets_are_still_eligible_to_scrobble():
    """The whole point of the estimated path: manual whole-set scrobbling."""
    ws = build_windows(_setlist(_track(1), _track(2)), 600)
    assert all(w.eligible for w in ws.windows)


# --- naming (precision over recall) -----------------------------------------


def test_a_resolved_track_scrobbles_its_canonical_lastfm_name():
    track = _track(1, "0:00", artists=("100 Gecs",), title="Dumbest Girl Alive")
    track.resolution = Resolution(
        status="resolved", lastfm=LastfmMatch(artist="100 gecs", track="Dumbest Girl Alive")
    )
    w = build_windows(_setlist(track), 600).windows[0]

    assert (w.scrobble_artist, w.scrobble_track) == ("100 gecs", "Dumbest Girl Alive")
    assert w.canonical is True


def test_an_unmatched_track_falls_back_to_normalized_names_never_raw_text():
    track = _track(1, "0:00", artists=("A", "B"), title="Song")
    track.raw_text = "A & B - Song [some 1001TL noise]"
    w = build_windows(_setlist(track), 600).windows[0]

    assert w.scrobble_track == "Song" and w.canonical is False
    assert "noise" not in (w.scrobble_track or "")


def test_an_unmatched_track_scrobbles_only_the_primary_artist():
    """Joining credits would scrobble one literal artist named
    'Knock2, Sophia Gripari', which links to nobody's page."""
    track = _track(1, "0:00", artists=("Knock2", "Sophia Gripari"), title="my melody")
    w = build_windows(_setlist(track), 600).windows[0]
    assert w.scrobble_artist == "Knock2"


def test_an_unmatched_track_keeps_its_version_in_the_title():
    """A VIP/edit is a different performance; logging it as the original
    would misattribute the play."""
    track = _track(1, "0:00", artists=("Knock2",), title="my melody", remix="VIP")
    w = build_windows(_setlist(track), 600).windows[0]

    assert w.scrobble_track == "my melody (VIP)"
    assert w.label == "Knock2 – my melody (VIP)"


def test_a_canonical_match_is_used_verbatim_and_not_rewritten():
    """Last.fm's own spelling wins — we never append our remix to it."""
    track = _track(1, "0:00", artists=("Knock2", "Sophia Gripari"), title="my melody", remix="VIP")
    track.resolution = Resolution(
        status="resolved", lastfm=LastfmMatch(artist="Knock2", track="my melody")
    )
    w = build_windows(_setlist(track), 600).windows[0]

    assert (w.scrobble_artist, w.scrobble_track) == ("Knock2", "my melody")
    assert w.canonical is True


# --- mashups ----------------------------------------------------------------


def _mashup_track(position, cue):
    return SetlistTrack(
        position=position, cue_time=cue, raw_text="A - X vs. B - Y",
        artists=["A", "B"], title="X vs. Y",
        mashup_components=[
            {"artists": ["A"], "title": "X"},
            {"artists": ["B"], "title": "Y", "remix": "Edit"},
        ],
    )


def test_a_mashup_parent_is_never_scrobbled():
    """'X vs. Y' is a blob, not a track — scrobbling it would invent an entry."""
    ws = build_windows(_setlist(_mashup_track(1, "0:00"), _track(2, "5:00")), 900)
    parent = ws.windows[0]
    assert parent.component_index is None
    assert parent.eligible is False and "components scrobble separately" in parent.reason


def test_mashup_components_get_their_own_scrobbles_in_the_parents_window():
    ws = build_windows(
        _setlist(_mashup_track(1, "0:00"), _track(2, "5:00")), 900,
        ScrobbleConfig(mashups="all"),
    )
    components = [w for w in ws.windows if w.component_index is not None]

    assert len(components) == 2
    assert [(w.scrobble_artist, w.scrobble_track) for w in components] == [
        ("A", "X"),
        ("B", "Y (Edit)"),
    ]
    # They share the parent's slice of the recording.
    assert all((w.start_s, w.end_s) == (0, 300) for w in components)
    assert all(w.position == 1 for w in components)
    assert all(w.eligible for w in components)


def test_a_component_with_a_lastfm_match_scrobbles_canonically():
    track = _mashup_track(1, "0:00")
    track.mashup_components[0].resolution = Resolution(
        status="resolved", lastfm=LastfmMatch(artist="A Canonical", track="X Canonical")
    )
    ws = build_windows(_setlist(track, _track(2, "5:00")), 900)
    component = [w for w in ws.windows if w.component_index == 0][0]

    assert (component.scrobble_artist, component.scrobble_track) == ("A Canonical", "X Canonical")
    assert component.canonical is True


# --- eligibility ------------------------------------------------------------


def test_unreleased_ids_are_not_scrobblable():
    track = SetlistTrack(position=1, cue_time="0:00", raw_text="ID - ID", is_id=True)
    w = build_windows(_setlist(track, _track(2, "5:00")), 900).windows[0]
    assert w.eligible is False and w.reason == "unreleased"
    assert w.label == "ID · unreleased"


def test_a_window_shorter_than_the_lastfm_minimum_is_not_eligible():
    ws = build_windows(_setlist(_track(1, "0:00"), _track(2, "0:20"), _track(3, "5:00")), 900)
    assert ws.windows[0].eligible is False
    assert ws.windows[0].reason == f"window under {MIN_WINDOW_S}s"
    assert ws.windows[1].eligible is True


def test_a_track_with_no_title_is_not_scrobblable():
    track = SetlistTrack(position=1, cue_time="0:00", raw_text="???", artists=[])
    w = build_windows(_setlist(track, _track(2, "5:00")), 900).windows[0]
    assert w.eligible is False and w.reason == "no track name"


# --- the set is the album ---------------------------------------------------


def test_the_set_is_the_album_and_the_dj_its_artist():
    sets = _setlist(_track(1, "0:00"), _track(2, "5:00"))
    sets.title = "ISOKNOCK @ 4EVR FINALE, EDC Las Vegas 2025-05-17"
    sets.dj_names = ["ISOKNOCK"]
    ws = build_windows(sets, 900)

    assert ws.album == "ISOKNOCK @ 4EVR FINALE, EDC Las Vegas 2025-05-17"
    assert ws.album_artist == "ISOKNOCK"


def test_the_album_artist_is_the_primary_dj_of_a_b2b():
    """A joined string would be a literal artist nobody's page — same rule as
    track artists."""
    sets = _setlist(_track(1, "0:00"), _track(2, "5:00"))
    sets.dj_names = ["Fred again..", "Sammy Virji"]
    assert build_windows(sets, 900).album_artist == "Fred again.."


def test_a_set_without_a_title_or_dj_has_no_album():
    sets = _setlist(_track(1, "0:00"), _track(2, "5:00"))
    sets.title = None
    sets.dj_names = []
    ws = build_windows(sets, 900)
    assert ws.album is None and ws.album_artist is None


# --- scrobble config (design §4.3) ------------------------------------------


def test_defaults_are_the_cautious_reading():
    c = ScrobbleConfig()
    assert (c.layered, c.mashups, c.unreleased, c.unmatched) == (
        "skip", "primary", "skip", "scrobble",
    )


def test_layered_rows_are_skipped_by_default_and_can_be_enabled():
    sets = _setlist(_track(1, "0:00"), _track(2, "5:00", played_with=1), _track(3, "9:00"))

    default = build_windows(sets, 900).windows[1]
    assert default.eligible is False and "layered" in default.reason

    on = build_windows(sets, 900, ScrobbleConfig(layered="scrobble")).windows[1]
    assert on.eligible is True


def test_only_the_primary_mashup_component_scrobbles_by_default():
    ws = build_windows(_setlist(_mashup_track(1, "0:00"), _track(2, "5:00")), 900)
    components = [w for w in ws.windows if w.component_index is not None]

    assert components[0].eligible is True
    assert components[1].eligible is False
    assert "primary component only" in components[1].reason


def test_mashups_can_be_skipped_entirely():
    ws = build_windows(
        _setlist(_mashup_track(1, "0:00"), _track(2, "5:00")), 900,
        ScrobbleConfig(mashups="skip"),
    )
    assert not any(w.eligible for w in ws.windows if w.position == 1)


def test_a_layered_mashup_skips_its_components_too():
    """The row-level setting has to reach the components, or 'skip w/' leaks."""
    track = _mashup_track(1, "0:00")
    track.played_with = 0
    ws = build_windows(_setlist(track, _track(2, "5:00")), 900)
    assert not any(w.eligible for w in ws.windows if w.position == 1)


def test_unmatched_tracks_can_be_skipped():
    sets = _setlist(_track(1, "0:00"), _track(2, "5:00"))
    assert build_windows(sets, 900).windows[0].eligible is True

    strict = build_windows(sets, 900, ScrobbleConfig(unmatched="skip")).windows[0]
    assert strict.eligible is False and "no Last.fm match" in strict.reason


def test_skipping_unmatched_keeps_canonical_matches():
    track = _track(1, "0:00")
    track.resolution = Resolution(status="resolved", lastfm=LastfmMatch(artist="A", track="T"))
    ws = build_windows(_setlist(track, _track(2, "5:00")), 900, ScrobbleConfig(unmatched="skip"))
    assert ws.windows[0].eligible is True


def test_a_track_with_no_artist_is_not_scrobblable():
    """Last.fm needs both fields; a title alone would scrobble under no artist."""
    track = SetlistTrack(position=1, cue_time="0:00", raw_text="? - Song", artists=[], title="Song")
    w = build_windows(_setlist(track, _track(2, "5:00")), 900).windows[0]
    assert w.eligible is False and w.reason == "no track name"


# --- edges ------------------------------------------------------------------


def test_an_empty_setlist_produces_no_windows():
    ws = build_windows(_setlist())
    assert ws.windows == [] and ws.live_capable is False


def test_windows_come_back_in_playing_order_even_if_tracks_are_not():
    ws = build_windows(_setlist(_track(3, "9:00"), _track(1, "0:00"), _track(2, "5:00")), 900)
    assert [w.position for w in ws.windows] == [1, 2, 3]
