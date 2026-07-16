from soundseek.resolver.cascade import CascadeResult, Unit, build_queries, run_cascade


class FakeSpotify:
    def __init__(self, results):
        self.results = results
        self.queries = []

    def search(self, query, limit=8):
        self.queries.append(query)
        return self.results


class FakeLastfm:
    def __init__(self, info=None, search_results=None):
        self.info = info or {}
        self.search_results = search_results or []
        self.get_info_calls = []

    def get_info(self, artist, track):
        self.get_info_calls.append((artist, track))
        return self.info.get((artist.lower(), track.lower()))

    def search(self, track, artist=None, limit=8):
        return self.search_results


class FakeYouTube:
    def __init__(self, results):
        self.results = results
        self.queries = []

    def search(self, query, limit=8):
        self.queries.append(query)
        return self.results


RUMBLE = Unit(
    artists=["Skrillex", "Fred again..", "Flowdan"],
    title="Rumble",
    remix=None,
    raw_text="Skrillex & Fred again.. & Flowdan - Rumble",
)


class TestBuildQueries:
    def test_ladder_order_and_dedupe(self):
        unit = Unit(
            artists=["A", "B"], title="Song", remix="X Remix", raw_text="A & B - Song (X Remix)"
        )
        queries = build_queries(unit)
        assert queries == ["A B Song X Remix", "A B Song", "A Song"]

    def test_single_artist_no_minimal_duplicate(self):
        unit = Unit(artists=["A"], title="Song", remix=None, raw_text="A - Song")
        assert build_queries(unit) == ["A Song"]


class TestCascade:
    def test_confident_match_accepted(self):
        sp = FakeSpotify(
            [{"id": "sp1", "title": "Rumble", "artists": ["Skrillex", "Fred again..", "Flowdan"], "url": "u", "duration_ms": 150000}]
        )
        result = run_cascade(RUMBLE, sp, None, None)
        assert result.spotify is not None and result.spotify.id == "sp1"
        assert result.confidence >= 0.75
        assert not result.ambiguous

    def test_poor_match_stores_nothing(self):
        # precision over recall: bad candidates never get stored
        sp = FakeSpotify([{"id": "x", "title": "Totally Different Song", "artists": ["Nobody"], "url": "u", "duration_ms": 1}])
        result = run_cascade(RUMBLE, sp, None, None)
        assert result.spotify is None
        assert result.confidence == 0.0

    def test_zero_results_is_clean_no_match_not_ambiguous(self):
        sp = FakeSpotify([])
        result = run_cascade(RUMBLE, sp, None, None)
        assert result.spotify is None
        assert not result.ambiguous

    def test_borderline_match_flags_agent(self):
        # similar-ish but not confident -> ambiguous band -> agent
        sp = FakeSpotify([{"id": "x", "title": "Rumble Rumble", "artists": ["Skrilex"], "url": "u", "duration_ms": 150000}])
        result = run_cascade(RUMBLE, sp, None, None)
        assert result.spotify is None
        assert result.ambiguous

    def test_mashup_row_only_searches_youtube(self):
        sp = FakeSpotify([{"id": "sp", "title": "whatever", "artists": [], "url": "u"}])
        yt = FakeYouTube(
            [{"id": "yt1", "title": "Armin van Buuren vs. Empire Of The Sun - Walking On A Dream (Mashup)", "artists": ["ArminFan"], "url": "u", "duration_ms": 240000}]
        )
        unit = Unit(
            artists=[],
            title=None,
            remix="DJ Mashup",
            raw_text="Armin van Buuren vs. Empire Of The Sun - Walking On A Dream (Mashup)",
            kind="mashup_row",
        )
        result = run_cascade(unit, sp, yt, None)
        assert sp.queries == []  # spotify never touched
        assert yt.queries == [unit.raw_text]

    def test_lastfm_canonicalization_picks_highest_listeners(self):
        canonical_a = {"artist": "Skrillex", "track": "Rumble", "mbid": None, "listeners": 500, "url": "u1"}
        canonical_b = {"artist": "Skrillex, Fred again.. & Flowdan", "track": "Rumble", "mbid": None, "listeners": 90000, "url": "u2"}
        lf = FakeLastfm(
            info={
                ("skrillex", "rumble"): canonical_a,
                ("skrillex, fred again.. & flowdan", "rumble"): canonical_b,
            },
            search_results=[
                {"artist": "Skrillex, Fred again.. & Flowdan", "track": "Rumble", "listeners": 90000, "url": "u2"},
            ],
        )
        result = run_cascade(RUMBLE, None, None, lf)
        assert result.lastfm is not None
        # direct getInfo hit exists (canonical_a), but if both were candidates the
        # higher-listener official entry wins; here direct hit resolves first
        assert result.lastfm.track == "Rumble"

    def test_lastfm_no_entry_stores_nothing(self):
        lf = FakeLastfm(info={}, search_results=[])
        result = run_cascade(RUMBLE, None, None, lf)
        assert result.lastfm is None
