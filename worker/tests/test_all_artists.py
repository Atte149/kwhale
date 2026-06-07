"""Unit tests for collaborative-artist parsing.

We exercise extract_all_artists() directly (no FLAC, no mutagen) so these
tests stay fast and run without the worker image.
"""
from app.tagging import extract_all_artists, _split_artists_tag, _split_feat_list


class TestSplitArtistsTag:
    def test_single_string_semicolon(self):
        assert _split_artists_tag("Illumate; Iroh; Найкер") == [
            "Illumate", "Iroh", "Найкер"
        ]

    def test_list_of_strings(self):
        # mutagen easy=True form: one tag value per write.
        assert _split_artists_tag(["Illumate; Iroh", "Найкер"]) == [
            "Illumate", "Iroh", "Найкер"
        ]

    def test_case_and_whitespace_folded(self):
        assert _split_artists_tag("  Babymetal ; BABYMETAL ; Bring Me the Horizon ") == [
            "Babymetal", "Bring Me the Horizon"
        ]

    def test_empty(self):
        assert _split_artists_tag("") == []
        assert _split_artists_tag(None) == []
        assert _split_artists_tag([]) == []


class TestSplitFeatList:
    def test_ampersand(self):
        assert _split_feat_list("Demi Lovato & blackbear") == [
            "Demi Lovato", "blackbear"
        ]

    def test_and_word(self):
        assert _split_feat_list("Demi Lovato and blackbear") == [
            "Demi Lovato", "blackbear"
        ]

    def test_comma(self):
        assert _split_feat_list("X, Y, Z") == ["X", "Y", "Z"]

    def test_mixed(self):
        assert _split_feat_list("X & Y, Z and W") == ["X", "Y", "Z", "W"]

    def test_empty(self):
        assert _split_feat_list("") == []
        assert _split_feat_list("   ") == []


class TestExtractAllArtists:
    def test_vorbis_artists_tag_wins(self):
        # The Picard/beets `artists` tag is the source of truth; the single
        # `artist` tag is also added but should not duplicate the first.
        # The title is ignored because `artists` is already complete.
        out = extract_all_artists(
            artists_tag="Illumate; Iroh; Найкер",
            artist_tag="Illumate",
            title="Бастарделла (feat. IROH & Найкер)",
        )
        assert out == ["Illumate", "Iroh", "Найкер"]

    def test_falls_back_to_artist_tag_then_title(self):
        # No `artists` tag — common for older libraries. We use the
        # `artist` tag for the primary, then scrape the title for
        # the featured artists.
        out = extract_all_artists(
            artists_tag=None,
            artist_tag="All Time Low",
            title="Monsters (feat. Demi Lovato and blackbear)",
        )
        assert out == ["All Time Low", "Demi Lovato", "blackbear"]

    def test_title_only_no_artist_tag(self):
        # Edge case: only a title with a feat block. Should still extract
        # the featured artists so the track is findable.
        out = extract_all_artists(
            artists_tag=None,
            artist_tag=None,
            title="Some Song (feat. Lonely Artist)",
        )
        assert out == ["Lonely Artist"]

    def test_empty_inputs(self):
        assert extract_all_artists(None, None, None) == []
        assert extract_all_artists("", "", "") == []

    def test_artists_tag_list_form(self):
        out = extract_all_artists(
            artists_tag=["Bring Me the Horizon; Babymetal"],
            artist_tag="Bring Me the Horizon",
            title="Kingslayer (feat. BABYMETAL)",
        )
        # The tag is the truth — title is ignored when we already have
        # a complete collaboration list.
        assert out == ["Bring Me the Horizon", "Babymetal"]

    def test_no_feat_in_title(self):
        out = extract_all_artists(
            artists_tag="Bring Me the Horizon",
            artist_tag="Bring Me the Horizon",
            title="Kingslayer",
        )
        assert out == ["Bring Me the Horizon"]

    def test_brackets_and_ft_synonym(self):
        # No `artists` tag — title is parsed for the feat block.
        out = extract_all_artists(
            artists_tag=None,
            artist_tag="Main",
            title="Song [ft. Featured One]",
        )
        assert out == ["Main", "Featured One"]

    def test_title_list_input(self):
        # mutagen easy=True returns lists for multi-value tags.
        out = extract_all_artists(
            artists_tag=None,
            artist_tag="Main",
            title=["Song (feat. A & B)", "ignore second"],
        )
        assert out == ["Main", "A", "B"]

    def test_duplicate_feature(self):
        # "X feat. Y" plus artists=[Y] — Y should not appear twice.
        out = extract_all_artists(
            artists_tag="Main; Featured",
            artist_tag="Main",
            title="Track (feat. Featured)",
        )
        assert out == ["Main", "Featured"]

    def test_cyrillic_and_latin_mixed(self):
        out = extract_all_artists(
            artists_tag="ЗАМАЙ; ШУММ",
            artist_tag="ЗАМАЙ",
            title="Дверь (feat. ШУММ)",
        )
        assert out == ["ЗАМАЙ", "ШУММ"]

    def test_artists_tag_does_not_pick_up_title_artifacts(self):
        # When `artists` is already complete, we do NOT re-parse the
        # title — that would risk duplicates and false positives on
        # songs whose title coincidentally mentions a name in feat form.
        out = extract_all_artists(
            artists_tag="Main; Real",
            artist_tag="Main",
            title="Song (feat. Real & Imaginary)",
        )
        assert out == ["Main", "Real"]
