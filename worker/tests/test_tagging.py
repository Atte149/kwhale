"""Tests for tag cleaning and spectrogram-reply parsing.

These exercise the data path that previously let truncated-JSON garbage (the
literal key "tags", numeric fragments, etc.) reach the database.

Run: pytest worker/tests/test_tagging.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.tagging import clean_tags, parse_spectro


class TestCleanTags:
    def test_lowercases_and_dedupes(self):
        assert clean_tags(["Rock", "rock", "JAZZ"]) == ["rock", "jazz"]

    def test_drops_structural_stopwords(self):
        assert clean_tags(["tags", "desc", "rock"]) == ["rock"]

    def test_drops_numeric_fragments(self):
        assert clean_tags(["0.9", "rock", ":", "123"]) == ["rock"]

    def test_drops_overlong(self):
        long = "x" * 50
        assert clean_tags([long, "rock"]) == ["rock"]

    def test_caps_at_eight(self):
        out = clean_tags([f"tag{i}" for i in range(20)])
        assert len(out) == 8

    def test_strips_quotes_and_whitespace(self):
        assert clean_tags(['  "warm" ', "'bright'"]) == ["warm", "bright"]

    def test_empty(self):
        assert clean_tags([]) == []

    def test_mixed_types(self):
        # Non-string items shouldn't crash; numbers get coerced then dropped
        # (no alpha chars), real tags survive.
        assert clean_tags([1, "rock", 2.5, None]) == ["rock"]


class TestParseSpectro:
    def test_clean_object(self):
        out = parse_spectro('{"desc":"warm analog","tags":["bass","warm"]}')
        assert out["desc"] == "warm analog"
        assert out["tags"] == ["bass", "warm"]

    def test_truncated_no_key_leak(self):
        # The exact regression: truncated mid-array used to yield ["tags","rock",...]
        out = parse_spectro('{"desc":"x","tags":["rock","ener')
        assert "tags" not in out["tags"]
        assert out["tags"] == ["rock"]

    def test_truncated_keeps_desc(self):
        out = parse_spectro('{"desc":"deep bass, bright highs","tags":["bass"')
        assert out["desc"] == "deep bass, bright highs"
        assert out["tags"] == ["bass"]

    def test_extra_keys_excluded(self):
        out = parse_spectro('{"desc":"d","tags":["rock"],"confidence":0.9}')
        assert out["tags"] == ["rock"]

    def test_empty(self):
        assert parse_spectro("") == {"desc": None, "tags": []}

    def test_garbage(self):
        out = parse_spectro("the model refused to answer")
        assert out["tags"] == []
