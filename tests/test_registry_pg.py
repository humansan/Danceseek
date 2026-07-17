"""PgRegistry parity: same dedupe priority as the JSON Registry, but backed by
the tracks table. A fake in-memory db (all_tracks/upsert_track) keeps these off
the real Neon DB while exercising the load-all / write-through seams."""

import pytest

from soundseek import db
from soundseek.models import LastfmMatch, PlatformMatch, Resolution
from soundseek.registry_pg import PgRegistry


@pytest.fixture
def fake_tracks(monkeypatch):
    store: dict[str, object] = {}

    def all_tracks():
        return [rec.model_copy(deep=True) for rec in store.values()]

    def upsert_track(rec):
        store[rec.id] = rec.model_copy(deep=True)

    monkeypatch.setattr(db, "all_tracks", all_tracks)
    monkeypatch.setattr(db, "upsert_track", upsert_track)
    return store


def _resolution(spotify_id=None, lastfm=None, status="resolved"):
    return Resolution(
        status=status,
        spotify=PlatformMatch(id=spotify_id, title="Rumble", artists=["Skrillex"], url="u")
        if spotify_id
        else None,
        lastfm=LastfmMatch(artist=lastfm[0], track=lastfm[1], listeners=100) if lastfm else None,
        confidence=0.9,
    )


ARTISTS = ["Skrillex", "Fred again..", "Flowdan"]


def test_write_through_and_reload(fake_tracks):
    reg = PgRegistry()
    rec = reg.find_or_create(ARTISTS, "Rumble", None, _resolution(spotify_id="sp1"))
    assert fake_tracks[rec.id].spotify_id == "sp1"  # written through immediately

    reg2 = PgRegistry()  # fresh instance reloads from the (fake) table
    hit = reg2.lookup(ARTISTS, "Rumble", None)
    assert hit is not None and hit.id == rec.id and hit.spotify_id == "sp1"


def test_dedupe_by_spotify_id(fake_tracks):
    reg = PgRegistry()
    a = reg.find_or_create(ARTISTS, "Rumble", None, _resolution(spotify_id="sp1"))
    b = reg.find_or_create(["Skrillex", "Fred Again"], "RUMBLE", None, _resolution(spotify_id="sp1"))
    assert a.id == b.id


def test_dedupe_by_lastfm_pair(fake_tracks):
    reg = PgRegistry()
    a = reg.find_or_create(ARTISTS, "Rumble", None, _resolution(lastfm=("Skrillex", "Rumble")))
    b = reg.find_or_create(["Skrillex"], "rumble!!", None, _resolution(lastfm=("Skrillex", "Rumble")))
    assert a.id == b.id


def test_enrich_never_overwrites_existing_id(fake_tracks):
    reg = PgRegistry()
    a = reg.find_or_create(ARTISTS, "Rumble", None, _resolution(spotify_id="sp1"))
    reg.find_or_create(ARTISTS, "Rumble", None, _resolution(spotify_id="sp2"))
    assert reg.get(a.id).spotify_id == "sp1"


def test_unreleased_round_trip(fake_tracks):
    reg = PgRegistry()
    rec = reg.find_or_create(["Fred again.."], "ID", None, Resolution(status="unreleased"))
    rebuilt = reg.resolution_from_record(rec)
    assert rec.is_unreleased
    assert rebuilt.status == "unreleased" and rebuilt.method == "registry"
