from soundseek.resolver.gather import Unit, UnitCandidates, build_query, gather
from soundseek.resolver.picker import UnitPick, apply_pick


class FakeSpotify:
    def __init__(self, results_by_call=None):
        self.results_by_call = results_by_call or [[]]
        self.queries = []

    def search(self, query, limit=8):
        self.queries.append(query)
        return self.results_by_call[min(len(self.queries) - 1, len(self.results_by_call) - 1)]


class FakeYouTube:
    def __init__(self, results):
        self.results = results
        self.queries = []

    def search(self, query, limit=8):
        self.queries.append(query)
        return self.results


class FakeClients:
    def __init__(self, spotify=None, youtube=None, lastfm=None):
        self.spotify = spotify
        self.youtube = youtube
        self.lastfm = lastfm


RUMBLE = Unit(
    artists=["Skrillex", "Fred again..", "Flowdan"],
    title="Rumble",
    remix="Extended Mix",
    raw_text="Skrillex & Fred again.. & Flowdan - Rumble (Extended Mix)",
)

SP_CAND = {"id": "sp1", "title": "Rumble - Extended Mix", "artists": ["Skrillex"], "url": "u", "duration_ms": 200000}
YT_CAND = {"id": "yt1", "title": "Rumble (Extended)", "artists": ["Skrillex"], "url": "u", "duration_ms": 200000}
LF_CAND = {"artist": "Skrillex", "track": "Rumble", "mbid": None, "listeners": 90000, "url": "u"}


class TestBuildQuery:
    def test_variant_kept_in_query(self):
        # user requirement: keep "Extended Mix" etc. — platforms list those variants
        assert build_query(RUMBLE) == "Skrillex Fred again.. Flowdan Rumble Extended Mix"


class TestGather:
    def test_spotify_fallback_without_remix_on_empty(self):
        sp = FakeSpotify(results_by_call=[[], [SP_CAND]])
        uc = gather(RUMBLE, FakeClients(spotify=sp))
        assert len(sp.queries) == 2
        assert "Extended Mix" not in sp.queries[1]
        assert uc.spotify == [SP_CAND]

    def test_mashup_row_youtube_only_with_raw_text(self):
        sp = FakeSpotify([[SP_CAND]])
        yt = FakeYouTube([YT_CAND])
        unit = Unit([], None, None, "A vs. B - X (Mashup)", kind="mashup_row")
        uc = gather(unit, FakeClients(spotify=sp, youtube=yt))
        assert sp.queries == []
        assert yt.queries == ["A vs. B - X (Mashup)"]
        assert uc.spotify == []


def _uc():
    return UnitCandidates(unit=RUMBLE, spotify=[SP_CAND], youtube=[YT_CAND], lastfm=[LF_CAND])


ALL = ["spotify", "lastfm", "youtube"]


class TestApplyPick:
    def test_valid_picks_resolved(self):
        pick = UnitPick(unit=1, spotify="S1", youtube="Y1", lastfm="L1", confidence=0.9)
        res = apply_pick(_uc(), pick, ALL)
        assert res.status == "resolved"
        assert res.spotify.id == "sp1" and res.youtube.id == "yt1"
        assert res.lastfm.artist == "Skrillex"
        assert res.method == "batch_llm"

    def test_low_confidence_discards_everything(self):
        pick = UnitPick(unit=1, spotify="S1", youtube="Y1", lastfm="L1", confidence=0.5)
        res = apply_pick(_uc(), pick, ALL)
        assert res.status == "no_match"
        assert res.spotify is None and res.youtube is None and res.lastfm is None

    def test_hallucinated_keys_discarded(self):
        pick = UnitPick(unit=1, spotify="S9", youtube="banana", lastfm=None, confidence=0.95)
        res = apply_pick(_uc(), pick, ALL)
        assert res.status == "no_match"
        assert res.spotify is None and res.youtube is None

    def test_partial_when_some_platforms_null(self):
        pick = UnitPick(unit=1, spotify=None, youtube="Y1", lastfm="L1", confidence=0.9)
        res = apply_pick(_uc(), pick, ALL)
        assert res.status == "partial"

    def test_applicable_respects_disabled_clients(self):
        # spotify client disabled -> lastfm+youtube alone can be "resolved"
        pick = UnitPick(unit=1, spotify=None, youtube="Y1", lastfm="L1", confidence=0.9)
        res = apply_pick(_uc(), pick, ["lastfm", "youtube"])
        assert res.status == "resolved"

    def test_missing_pick_is_no_match(self):
        res = apply_pick(_uc(), None, ALL)
        assert res.status == "no_match" and res.confidence == 0.0
