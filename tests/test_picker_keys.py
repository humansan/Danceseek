"""How the model's answer maps back to a gathered candidate.

Regression: a Last.fm-only run matched 0/17 tracks because the model answered
with the candidate's *text* ("100 gecs - Dumbest Girl Alive") instead of its
key ("L1"), and the strict parser dropped every one of them silently.
"""

import pytest

from soundseek.resolver.gather import Unit, UnitCandidates
from soundseek.resolver.picker import UnitPick, _key_to_candidate, apply_pick

LASTFM = [
    {"artist": "100 gecs", "track": "Dumbest Girl Alive", "listeners": 4200, "mbid": None, "url": "u"},
    {"artist": "The Prodigy", "track": "Firestarter", "listeners": 900, "mbid": None, "url": "u2"},
]
SPOTIFY = [{"id": "sp1", "title": "Moaner", "artists": ["Underworld"], "url": "u", "duration_ms": 60000}]
YOUTUBE = [{"id": "yt1", "title": "Residual Stress", "artists": ["chan"], "url": "u", "duration_ms": 60000}]


@pytest.mark.parametrize(
    "answer",
    [
        "L1",                                                  # documented form
        "l1",                                                  # lowercase
        " L1 ",                                                # padded
        "1",                                                   # bare index
        "L1: 100 gecs - Dumbest Girl Alive",                   # key + label
        "100 gecs - Dumbest Girl Alive",                       # the regression
        "100 GECS - dumbest girl alive",                       # case-insensitive
        "100 gecs - Dumbest Girl Alive (listeners=4200)",      # the full rendered line
    ],
)
def test_every_form_the_model_actually_emits_resolves(answer):
    assert _key_to_candidate(answer, LASTFM, "lastfm") is LASTFM[0]


@pytest.mark.parametrize("answer", [None, "", "L9", "0", "L0", "Some Other Song", "n/a", "none"])
def test_anything_that_names_no_candidate_is_still_discarded(answer):
    """Precision over recall: we never guess at an unrecognised answer."""
    assert _key_to_candidate(answer, LASTFM, "lastfm") is None


def test_text_answers_work_for_spotify_and_youtube_too():
    assert _key_to_candidate("Underworld - Moaner", SPOTIFY, "spotify") is SPOTIFY[0]
    assert _key_to_candidate("Residual Stress", YOUTUBE, "youtube") is YOUTUBE[0]
    # A YouTube candidate is identified by its title, not another platform's shape.
    assert _key_to_candidate("chan - Residual Stress", YOUTUBE, "youtube") is None


def test_the_second_candidate_is_reachable_by_text():
    assert _key_to_candidate("The Prodigy - Firestarter", LASTFM, "lastfm") is LASTFM[1]


# --- end to end through apply_pick ------------------------------------------


def _uc() -> UnitCandidates:
    uc = UnitCandidates(unit=Unit(["100 Gecs"], "Dumbest Girl Alive", None, "100 Gecs - Dumbest Girl Alive"))
    uc.lastfm = list(LASTFM)
    return uc


def test_a_text_answer_now_produces_a_real_match():
    pick = UnitPick(unit=1, lastfm="100 gecs - Dumbest Girl Alive", confidence=1.0)
    resolution = apply_pick(_uc(), pick, ["lastfm"])

    assert resolution.status == "resolved"
    assert resolution.lastfm.artist == "100 gecs"  # canonical Last.fm spelling
    assert resolution.notes is None


def test_an_unusable_answer_is_dropped_but_recorded():
    """The original bug was silent. Now the discard leaves a trail."""
    pick = UnitPick(unit=1, lastfm="Some Song We Never Searched", confidence=1.0)
    resolution = apply_pick(_uc(), pick, ["lastfm"])

    assert resolution.status == "no_match"
    assert resolution.notes and "lastfm=" in resolution.notes


def test_low_confidence_still_wins_over_a_parseable_key():
    pick = UnitPick(unit=1, lastfm="L1", confidence=0.1)
    assert apply_pick(_uc(), pick, ["lastfm"]).status == "no_match"
