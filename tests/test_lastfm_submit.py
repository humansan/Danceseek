"""Submitting plays to Last.fm: batching, parameter shape, result parsing."""

import pytest

from soundseek.lastfm import submit


@pytest.fixture(autouse=True)
def credentials(monkeypatch):
    monkeypatch.setenv("LASTFM_API_KEY", "key")
    monkeypatch.setenv("LASTFM_SECRET", "secret")


@pytest.fixture
def calls(monkeypatch):
    sent = []

    class _R:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    def fake_post(url, data=None, timeout=None):
        sent.append(data)
        n = sum(1 for k in data if k.startswith("artist["))
        return _R(
            {
                "scrobbles": {
                    "@attr": {"accepted": n, "ignored": 0},
                    "scrobble": [{"ignoredMessage": {"code": "0", "#text": ""}}] * n,
                }
            }
        )

    monkeypatch.setattr(submit, "signed_call", lambda method, **p: fake_post(
        None, data={"method": method, **p}
    ).json())
    return sent


def _plays(n):
    return [submit.Play(artist=f"A{i}", track=f"T{i}", timestamp=1_700_000_000 + i) for i in range(n)]


def test_a_play_is_sent_with_indexed_parameters(calls):
    submit.scrobble("sk", _plays(1))
    sent = calls[0]
    assert sent["method"] == "track.scrobble" and sent["sk"] == "sk"
    assert sent["artist[0]"] == "A0" and sent["track[0]"] == "T0"
    assert sent["timestamp[0]"] == "1700000000"


def test_batches_respect_the_fifty_per_request_limit(calls):
    result = submit.scrobble("sk", _plays(120))
    assert len(calls) == 3
    assert [sum(1 for k in c if k.startswith("artist[")) for c in calls] == [50, 50, 20]
    assert result.accepted == 120


def test_nothing_to_submit_makes_no_calls(calls):
    assert submit.scrobble("sk", []).accepted == 0
    assert calls == []


def test_now_playing_sends_duration_when_known(calls):
    submit.update_now_playing("sk", "A", "T", duration_s=310)
    assert calls[0]["method"] == "track.updateNowPlaying"
    assert calls[0]["duration"] == "310"


def test_now_playing_omits_an_unknown_duration(calls):
    submit.update_now_playing("sk", "A", "T")
    assert "duration" not in calls[0]


# --- the set is the album ---------------------------------------------------


def test_a_play_carries_the_set_as_its_album(calls):
    submit.scrobble(
        "sk",
        [
            submit.Play(
                artist="Knock2", track="my melody (VIP)", timestamp=1_700_000_000,
                album="ISOKNOCK @ 4EVR FINALE, EDC Las Vegas 2025-05-17",
                album_artist="ISOKNOCK",
            )
        ],
    )
    sent = calls[0]
    assert sent["album[0]"] == "ISOKNOCK @ 4EVR FINALE, EDC Las Vegas 2025-05-17"
    assert sent["albumArtist[0]"] == "ISOKNOCK"
    # The track's own identity is untouched by the album.
    assert sent["artist[0]"] == "Knock2" and sent["track[0]"] == "my melody (VIP)"


def test_every_play_in_a_batch_gets_the_album(calls):
    plays = [
        submit.Play(artist=f"A{i}", track=f"T{i}", timestamp=1_700_000_000 + i, album="The Set")
        for i in range(3)
    ]
    submit.scrobble("sk", plays)
    assert [calls[0][f"album[{i}]"] for i in range(3)] == ["The Set"] * 3


def test_album_fields_are_omitted_when_unknown(calls):
    submit.scrobble("sk", _plays(1))
    assert "album[0]" not in calls[0] and "albumArtist[0]" not in calls[0]


def test_now_playing_also_carries_the_album(calls):
    submit.update_now_playing("sk", "A", "T", album="The Set", album_artist="DJ")
    assert calls[0]["album"] == "The Set" and calls[0]["albumArtist"] == "DJ"


def test_ignored_scrobbles_are_reported(monkeypatch):
    monkeypatch.setattr(
        submit, "signed_call",
        lambda method, **p: {
            "scrobbles": {
                "@attr": {"accepted": 1, "ignored": 1},
                "scrobble": [
                    {"ignoredMessage": {"code": "0", "#text": ""}},
                    {"ignoredMessage": {"code": "1", "#text": "Artist was ignored"}},
                ],
            }
        },
    )
    result = submit.scrobble("sk", _plays(2))
    assert result.accepted == 1 and result.ignored == 1
    assert result.problems == [(1, "Artist was ignored")]


def test_a_single_scrobble_response_object_is_handled(monkeypatch):
    """Last.fm unwraps the list when there's exactly one scrobble."""
    monkeypatch.setattr(
        submit, "signed_call",
        lambda method, **p: {
            "scrobbles": {
                "@attr": {"accepted": 1, "ignored": 0},
                "scrobble": {"ignoredMessage": {"code": "0", "#text": ""}},
            }
        },
    )
    assert submit.scrobble("sk", _plays(1)).accepted == 1
