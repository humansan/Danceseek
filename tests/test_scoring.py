from soundseek.resolver import scoring


class TestNormalize:
    def test_case_punctuation_accents(self):
        assert scoring.normalize("Fred again..") == "fred again"
        assert scoring.normalize("CA7RIEL & Paco Amoroso") == "ca7riel and paco amoroso"
        assert scoring.normalize("Beyoncé") == "beyonce"

    def test_feat_markers_dropped(self):
        assert scoring.normalize("Kid Cudi ft. MGMT") == scoring.normalize("Kid Cudi MGMT")
        assert scoring.normalize("A feat. B") == scoring.normalize("A B")

    def test_and_vs_ampersand(self):
        assert scoring.normalize("Above & Beyond") == scoring.normalize("Above and Beyond")


class TestRemixGeneric:
    def test_generic_qualifiers(self):
        assert scoring.remix_is_generic("Extended Mix")
        assert scoring.remix_is_generic("Radio Edit")
        assert scoring.remix_is_generic(None)

    def test_named_remixes_are_not_generic(self):
        assert not scoring.remix_is_generic("Hamdi Remix")
        assert not scoring.remix_is_generic("Skrillex & Fred again.. Edit")


class TestScoreTrackCandidate:
    ARTISTS = ["Skrillex", "Fred again..", "Flowdan"]

    def test_exact_match_scores_high(self):
        cand = {"title": "Rumble", "artists": ["Skrillex", "Fred again..", "Flowdan"]}
        assert scoring.score_track_candidate(self.ARTISTS, "Rumble", None, cand) > 0.9

    def test_wrong_track_scores_low(self):
        cand = {"title": "Something Else Entirely", "artists": ["Someone"]}
        assert scoring.score_track_candidate(self.ARTISTS, "Rumble", None, cand) < 0.4

    def test_generic_remix_not_required(self):
        # Spotify lists "Rumble", we parsed "Rumble (Extended Mix)" — still a match
        cand = {"title": "Rumble", "artists": ["Skrillex", "Fred again..", "Flowdan"]}
        score = scoring.score_track_candidate(self.ARTISTS, "Rumble", "Extended Mix", cand)
        assert score > 0.75

    def test_named_remix_required(self):
        # We want the Hamdi Remix; the plain original must be penalized
        cand = {"title": "Rumble", "artists": ["Skrillex", "Fred again..", "Flowdan"]}
        score = scoring.score_track_candidate(self.ARTISTS, "Rumble", "Hamdi Remix", cand)
        assert score < 0.6
        cand_remix = {"title": "Rumble (Hamdi Remix)", "artists": ["Skrillex", "Hamdi"]}
        assert (
            scoring.score_track_candidate(self.ARTISTS, "Rumble", "Hamdi Remix", cand_remix)
            > score
        )


class TestScoreLastfm:
    def test_single_artist_entry_matches_multi_artist_track(self):
        # Last.fm canonical entry often has one artist string
        cand = {"artist": "Skrillex", "track": "Rumble", "listeners": 200000}
        score = scoring.score_lastfm_candidate(
            ["Skrillex", "Fred again..", "Flowdan"], "Rumble", None, cand
        )
        assert score >= 0.75

    def test_wrong_artist_low(self):
        cand = {"artist": "Bob", "track": "Rumble", "listeners": 5}
        score = scoring.score_lastfm_candidate(["Skrillex"], "Rumble", None, cand)
        assert score < 0.75


class TestScoreYouTube:
    def test_official_video_scores_high(self):
        cand = {
            "title": "Skrillex, Fred again.. & Flowdan - Rumble [Official Music Video]",
            "artists": ["Skrillex"],
            "duration_ms": 150000,
        }
        score = scoring.score_youtube_candidate(
            ["Skrillex", "Fred again..", "Flowdan"], "Rumble", None, cand
        )
        assert score > 0.8

    def test_full_set_duration_penalized(self):
        cand = {
            "title": "Skrillex - Rumble (played in 2 hour set)",
            "artists": ["Random Uploads"],
            "duration_ms": 2 * 60 * 60 * 1000,
        }
        score = scoring.score_youtube_candidate(["Skrillex"], "Rumble", None, cand)
        assert score < 0.75
