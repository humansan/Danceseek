import pytest

from soundseek.config import settings
from soundseek.models import LastfmMatch, PlatformMatch, Resolution
from soundseek.registry import Registry


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path):
    original = settings.data_dir
    settings.data_dir = tmp_path
    yield
    settings.data_dir = original


def _resolution(spotify_id=None, lastfm=None):
    return Resolution(
        status="resolved",
        spotify=PlatformMatch(id=spotify_id, title="Rumble", artists=["Skrillex"], url="u")
        if spotify_id
        else None,
        lastfm=LastfmMatch(artist=lastfm[0], track=lastfm[1], listeners=100) if lastfm else None,
        confidence=0.9,
    )


ARTISTS = ["Skrillex", "Fred again..", "Flowdan"]


def test_create_and_lookup_by_parsed_fields():
    reg = Registry()
    rec = reg.find_or_create(ARTISTS, "Rumble", None, _resolution(spotify_id="sp1"))
    # fresh instance = reload from disk
    reg2 = Registry()
    hit = reg2.lookup(ARTISTS, "Rumble", None)
    assert hit is not None and hit.id == rec.id and hit.spotify_id == "sp1"


def test_dedupe_by_spotify_id_across_spellings():
    reg = Registry()
    a = reg.find_or_create(ARTISTS, "Rumble", None, _resolution(spotify_id="sp1"))
    # different raw spelling, same spotify id -> same record
    b = reg.find_or_create(["Skrillex", "Fred Again"], "RUMBLE", None, _resolution(spotify_id="sp1"))
    assert a.id == b.id


def test_dedupe_by_lastfm_pair():
    reg = Registry()
    a = reg.find_or_create(ARTISTS, "Rumble", None, _resolution(lastfm=("Skrillex", "Rumble")))
    b = reg.find_or_create(["Skrillex"], "rumble!!", None, _resolution(lastfm=("Skrillex", "Rumble")))
    assert a.id == b.id


def test_enrich_never_overwrites_existing_id():
    reg = Registry()
    a = reg.find_or_create(ARTISTS, "Rumble", None, _resolution(spotify_id="sp1"))
    b = reg.find_or_create(ARTISTS, "Rumble", None, _resolution(spotify_id="sp2"))
    # parsed-key match found the same record; first spotify id kept
    assert b.id == a.id
    assert reg.get(a.id).spotify_id == "sp1"


def test_unreleased_record_round_trip():
    reg = Registry()
    res = Resolution(status="unreleased", confidence=0.0)
    rec = reg.find_or_create(["Fred again.."], "ID", None, res)
    assert rec.is_unreleased
    rebuilt = reg.resolution_from_record(rec)
    assert rebuilt.status == "unreleased" and rebuilt.method == "registry"


def test_resolution_from_record_partial():
    reg = Registry()
    rec = reg.find_or_create(ARTISTS, "Rumble", None, _resolution(spotify_id="sp1"))
    rebuilt = reg.resolution_from_record(rec)
    assert rebuilt.status == "partial"  # spotify but no lastfm
    assert rebuilt.spotify.id == "sp1"
    assert rebuilt.lastfm is None
