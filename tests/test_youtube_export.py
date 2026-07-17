"""Retry / failure behavior of YouTubeExporter.add_videos (no network, no auth)."""

from dataclasses import dataclass

from soundseek.exporter.youtube import YouTubeExporter


@dataclass
class FakeResp:
    status_code: int
    text: str = ""
    reason_phrase: str = ""


class FakeHTTP:
    """Returns a scripted status code per video id (a list, consumed per attempt)."""

    def __init__(self, scripts: dict[str, list[int]]):
        self.scripts = {k: list(v) for k, v in scripts.items()}
        self.calls: list[str] = []

    def post(self, url, params=None, json=None):
        vid = json["snippet"]["resourceId"]["videoId"]
        self.calls.append(vid)
        code = self.scripts[vid].pop(0)
        text = "quotaExceeded" if code == 403 else ""
        return FakeResp(status_code=code, text=text, reason_phrase="Conflict" if code == 409 else "")


def _exporter(http):
    ex = object.__new__(YouTubeExporter)  # bypass __init__ (auth/network)
    ex._http = http
    return ex


def test_transient_409_is_retried_then_succeeds(monkeypatch):
    monkeypatch.setattr("soundseek.exporter.youtube.time.sleep", lambda *_: None)
    http = FakeHTTP({"v1": [409, 409, 200]})
    added, failures = _exporter(http).add_videos("PL", ["v1"])
    assert added == 1 and failures == []
    assert http.calls == ["v1", "v1", "v1"]  # two retries then success


def test_persistent_409_gives_up_and_skips(monkeypatch):
    monkeypatch.setattr("soundseek.exporter.youtube.time.sleep", lambda *_: None)
    http = FakeHTTP({"v1": [409, 409, 409, 409], "v2": [200]})
    added, failures = _exporter(http).add_videos("PL", ["v1", "v2"])
    assert added == 1  # v2 still added despite v1 failing
    assert failures == [("v1", "409 Conflict")]


def test_quota_stops_and_marks_remainder(monkeypatch):
    monkeypatch.setattr("soundseek.exporter.youtube.time.sleep", lambda *_: None)
    http = FakeHTTP({"v1": [200], "v2": [403], "v3": [200]})
    added, failures = _exporter(http).add_videos("PL", ["v1", "v2", "v3"])
    assert added == 1
    # v2 hit quota; v2 and v3 both marked, v3 never attempted (playlist URL survives)
    assert failures == [("v2", "quota_exceeded"), ("v3", "quota_exceeded")]
    assert "v3" not in http.calls
