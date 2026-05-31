"""Tests for the robust JSON extraction used to parse LLM replies.

Run: pytest worker/tests/test_json_utils.py -v
(works for the identical api/app/json_utils.py too — the logic is mirrored.)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.json_utils import (
    extract_json,
    extract_json_object,
    extract_json_array,
    extract_string_list,
)


class TestExtractJsonObject:
    def test_clean_object(self):
        assert extract_json_object('{"desc":"warm","n":1}') == {"desc": "warm", "n": 1}

    def test_markdown_fence(self):
        assert extract_json_object('```json\n{"a":1}\n```') == {"a": 1}

    def test_fence_no_lang(self):
        assert extract_json_object('```\n{"a":1}\n```') == {"a": 1}

    def test_prose_prefix(self):
        assert extract_json_object('Sure! {"a":1} done') == {"a": 1}

    def test_braces_inside_strings(self):
        # Braces and a stray close-brace inside the string must not confuse the
        # balanced-span scanner.
        assert extract_json_object('{"d":"a {weird} } string","n":1}') == {
            "d": "a {weird} } string", "n": 1,
        }

    def test_escaped_quotes(self):
        assert extract_json_object('{"d":"he said \\"hi\\""}') == {"d": 'he said "hi"'}

    def test_none_and_empty(self):
        assert extract_json_object(None) is None
        assert extract_json_object("") is None
        assert extract_json_object("no json here") is None

    def test_array_is_not_object(self):
        assert extract_json_object('["a","b"]') is None


class TestExtractJsonArray:
    def test_clean_array(self):
        assert extract_json_array('["a","b","c"]') == ["a", "b", "c"]

    def test_prose_prefix(self):
        assert extract_json_array('Here: ["a","b"]') == ["a", "b"]

    def test_object_is_not_array(self):
        assert extract_json_array('{"a":1}') is None


class TestExtractStringList:
    """The truncation-tolerant path — this is the regression guard for the
    old bug where the JSON key 'tags' leaked into the parsed values."""

    def test_clean_array(self):
        assert extract_string_list('["rock","jazz"]') == ["rock", "jazz"]

    def test_object_with_key(self):
        assert extract_string_list('{"tags":["rock","jazz"]}', key="tags") == ["rock", "jazz"]

    def test_truncated_array_does_not_leak_key(self):
        # THE BUG: truncated mid-array. 'tags' must NOT appear in the result.
        out = extract_string_list('{"desc":"x","tags":["rock","ener', key="tags")
        assert "tags" not in out
        assert out == ["rock"]

    def test_extra_keys_after_array_do_not_leak(self):
        # Keys following the tags array must not be captured as values.
        out = extract_string_list('{"tags":["rock"],"confidence":0.9}', key="tags")
        assert out == ["rock"]
        assert "confidence" not in out

    def test_desc_before_tags_not_captured(self):
        # The desc value precedes the tags array and must be excluded.
        out = extract_string_list('{"desc":"melancholic","tags":["sad","slow"]}', key="tags")
        assert out == ["sad", "slow"]
        assert "melancholic" not in out

    def test_numeric_items_coerced(self):
        assert extract_string_list("[1, 2, 3]") == ["1", "2", "3"]

    def test_empty(self):
        assert extract_string_list(None, key="tags") == []
        assert extract_string_list("", key="tags") == []
        assert extract_string_list("garbage", key="tags") == []


class TestExtractJson:
    def test_returns_object_or_array(self):
        assert extract_json('{"a":1}') == {"a": 1}
        assert extract_json('[1,2]') == [1, 2]

    def test_prefers_valid_parse(self):
        assert extract_json('text {"a":1} more') == {"a": 1}
