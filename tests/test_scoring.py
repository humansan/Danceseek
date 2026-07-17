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


class TestSimilarity:
    def test_identical_variants(self):
        assert scoring.similarity("Fred Again - Rumble", "Fred again.. - Rumble") > 0.9

    def test_unrelated(self):
        assert scoring.similarity("Rumble", "Sandstorm") < 0.5
