"""Scrobble endpoints: server authority, replay semantics, honest failure."""

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

import apps.api.main as main  # noqa: E402
from soundseek import db, session  # noqa: E402
from soundseek.lastfm import submit  # noqa: E402
from soundseek.models import (  # noqa: E402
    LastfmMatch,
    ParserInfo,
    Resolution,
    Setlist,
    SetlistTrack,
)

client = TestClient(main.app)
SET_ID = "11111111-1111-1111-1111-111111111111"
_META = {"status": "resolved", "coverage": None, "resolved_at": None, "created_at": None}


def _track(position, cue, artist, title, canonical=True, **kw):
    t = SetlistTrack(
        position=position, cue_time=cue, raw_text=f"{artist} - {title}",
        artists=[artist], title=title, **kw,
    )
    if canonical:
        t.resolution = Resolution(
            status="resolved", lastfm=LastfmMatch(artist=artist, track=title)
        )
    return t


def _setlist():
    return Setlist(
        id=SET_ID, source_url="u", title="A set", dj_names=["The DJ"],
        parser=ParserInfo(model="x"),
        tracks=[
            _track(1, "0:00", "Armin van Buuren", "Awake"),
            _track(2, "5:00", "Knock2", "my melody", canonical=False),
            SetlistTrack(position=3, cue_time="10:00", raw_text="ID - ID", is_id=True),
        ],
    )


@pytest.fixture(autouse=True)
def wiring(monkeypatch):
    """Signed in, with the database and Last.fm stubbed."""
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setenv("LASTFM_API_KEY", "key")
    monkeypatch.setenv("LASTFM_SECRET", "secret")

    state = {"submitted": [], "now_playing": [], "config": {}}

    monkeypatch.setattr(db, "get_by_id", lambda sid: (_setlist(), _META))
    monkeypatch.setattr(db, "session_key_for", lambda uid: "sk-live")
    monkeypatch.setattr(db, "get_scrobble_config", lambda uid: state["config"])
    monkeypatch.setattr(db, "set_scrobble_config", lambda uid, cfg: state.update(config=cfg))

    def fake_now_playing(key, artist, track, duration_s=None, album=None, album_artist=None):
        state["now_playing"].append((artist, track, album, album_artist))

    def fake_scrobble(key, plays):
        state["plays"].extend(plays)
        for p in plays:
            state["submitted"].append(
                {"artist": p.artist, "track": p.track,
                 "played_at": datetime.fromtimestamp(p.timestamp, tz=timezone.utc)}
            )
        return submit.SubmitResult(accepted=len(plays))

    state["plays"] = []
    monkeypatch.setattr(submit, "update_now_playing", fake_now_playing)
    monkeypatch.setattr(submit, "scrobble", fake_scrobble)

    client.cookies.set(session.COOKIE_NAME, session.sign({"uid": "user-1"}))
    yield state
    client.cookies.clear()


def _target(position=1, **kw):
    return {"setlist_id": SET_ID, "position": position, **kw}


# --- auth -------------------------------------------------------------------


def test_scrobbling_requires_a_connected_account():
    client.cookies.clear()
    assert client.post("/scrobble", json=_target()).status_code == 401
    assert client.post("/scrobble/now-playing", json=_target()).status_code == 401


# --- the server decides -----------------------------------------------------


def test_a_scrobble_uses_the_canonical_name_not_whatever_the_client_says(wiring):
    r = client.post("/scrobble", json=_target(1))
    assert r.status_code == 200 and r.json()["scrobbled"] is True
    assert r.json()["artist"] == "Armin van Buuren" and r.json()["track"] == "Awake"
    assert wiring["submitted"][0]["track"] == "Awake"


def test_an_unmatched_track_scrobbles_under_our_normalized_name(wiring):
    r = client.post("/scrobble", json=_target(2)).json()
    assert r["scrobbled"] is True and r["artist"] == "Knock2"


def test_an_unreleased_id_is_refused_with_its_reason():
    r = client.post("/scrobble", json=_target(3)).json()
    assert r["scrobbled"] is False and r["reason"] == "unreleased"


def test_an_unknown_position_is_404():
    assert client.post("/scrobble", json=_target(99)).status_code == 404


def test_settings_are_enforced_server_side(wiring):
    """A client can't scrobble something the user chose to skip."""
    wiring["config"] = {"unmatched": "skip"}
    r = client.post("/scrobble", json=_target(2)).json()
    assert r["scrobbled"] is False and "no Last.fm match" in r["reason"]
    assert wiring["submitted"] == []


# --- replays -----------------------------------------------------------------


def test_playing_a_track_again_scrobbles_it_again(wiring):
    """No scrobble log, by design: a replay is a second listen and should be
    recorded as one. The browser stops a single window firing twice; the server
    deliberately keeps no memory of plays at all."""
    assert client.post("/scrobble", json=_target(1)).json()["scrobbled"] is True
    assert client.post("/scrobble", json=_target(1)).json()["scrobbled"] is True
    assert len(wiring["submitted"]) == 2


def test_a_failed_submission_is_reported_not_swallowed(wiring, monkeypatch):
    def boom(key, plays):
        raise submit.LastfmAuthError("Last.fm is down")

    monkeypatch.setattr(submit, "scrobble", boom)
    r = client.post("/scrobble", json=_target(1)).json()
    assert r["scrobbled"] is False and "down" in r["reason"]


def test_no_play_is_persisted_anywhere(wiring):
    """The API must not grow a scrobble log back by accident."""
    import inspect

    source = inspect.getsource(main)
    for gone in ("record_scrobble", "mark_scrobble_failed", "scrobbled_keys", "session_id"):
        assert gone not in source, f"{gone} is back in the API"


# --- timestamps -------------------------------------------------------------


def test_a_future_timestamp_is_clamped_to_now(wiring):
    future = int((datetime.now(timezone.utc) + timedelta(days=2)).timestamp())
    client.post("/scrobble", json=_target(1, started_at=future))
    played = wiring["submitted"][0]["played_at"]
    assert played <= datetime.now(timezone.utc) + timedelta(seconds=2)


def test_a_supplied_start_time_is_honoured(wiring):
    stamp = int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp())
    client.post("/scrobble", json=_target(1, started_at=stamp))
    assert abs(wiring["submitted"][0]["played_at"].timestamp() - stamp) < 2


# --- now playing ------------------------------------------------------------


def test_now_playing_flags_the_track_without_recording_a_play(wiring):
    r = client.post("/scrobble/now-playing", json=_target(1)).json()
    assert r["scrobbled"] is False and r["track"] == "Awake"
    assert wiring["now_playing"] == [("Armin van Buuren", "Awake", "A set", "The DJ")]
    assert wiring["submitted"] == []  # a status, not a play


# --- the set is the album ---------------------------------------------------


def test_a_scrobble_carries_the_set_as_its_album(wiring):
    client.post("/scrobble", json=_target(1))
    play = wiring["plays"][0]
    assert play.album == "A set" and play.album_artist == "The DJ"
    # the track keeps its own identity
    assert play.artist == "Armin van Buuren" and play.track == "Awake"


def test_every_track_of_a_whole_set_shares_the_album(wiring):
    client.post(f"/setlists/{SET_ID}/scrobble-set", json={})
    assert wiring["plays"] and all(p.album == "A set" for p in wiring["plays"])
    assert all(p.album_artist == "The DJ" for p in wiring["plays"])


def test_now_playing_refuses_an_ineligible_track(wiring):
    assert client.post("/scrobble/now-playing", json=_target(3)).json()["reason"] == "unreleased"
    assert wiring["now_playing"] == []


# --- whole set --------------------------------------------------------------


def test_whole_set_scrobbles_every_eligible_track(wiring):
    r = client.post(f"/setlists/{SET_ID}/scrobble-set", json={}).json()
    assert r["submitted"] == 2 and r["accepted"] == 2  # the ID is skipped
    assert r["skipped"] == 1 and r["timing"] == "cue"


def test_whole_set_timestamps_follow_the_cue_order(wiring):
    client.post(f"/setlists/{SET_ID}/scrobble-set", json={"duration": 900})
    stamps = [s["played_at"] for s in wiring["submitted"]]
    assert stamps == sorted(stamps)
    assert all(s <= datetime.now(timezone.utc) + timedelta(seconds=2) for s in stamps)


def test_whole_set_can_be_run_again(wiring):
    """Nothing server-side prevents it; the button relabels to say "again"."""
    first = client.post(f"/setlists/{SET_ID}/scrobble-set", json={}).json()
    again = client.post(f"/setlists/{SET_ID}/scrobble-set", json={}).json()
    assert first["submitted"] == again["submitted"] == 2


# --- settings ---------------------------------------------------------------


def test_config_round_trips(wiring):
    assert client.get("/me/scrobble-config").json()["mashups"] == "primary"
    saved = client.put("/me/scrobble-config", json={"layered": "scrobble", "mashups": "all"}).json()
    assert saved["layered"] == "scrobble"
    assert client.get("/me/scrobble-config").json()["mashups"] == "all"


def test_a_corrupt_stored_config_falls_back_to_defaults(wiring):
    wiring["config"] = {"mashups": "nonsense"}
    assert client.get("/me/scrobble-config").json()["mashups"] == "primary"


def test_no_scrobble_response_leaks_the_lastfm_session_key():
    """`sk-live` is the account's permanent write credential."""
    bodies = [
        client.post("/scrobble", json=_target(1)).text,
        client.post("/scrobble/now-playing", json=_target(1)).text,
        client.post(f"/setlists/{SET_ID}/scrobble-set", json={}).text,
        client.get(f"/setlists/{SET_ID}/cues").text,
    ]
    assert all("sk-live" not in body for body in bodies)


def test_cues_reflect_the_users_settings(wiring):
    wiring["config"] = {"unmatched": "skip"}
    windows = client.get(f"/setlists/{SET_ID}/cues").json()["windows"]
    unmatched = [w for w in windows if w["position"] == 2][0]
    assert unmatched["eligible"] is False
