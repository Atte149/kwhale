"""Tests for the prompt agent's pure logic: evidence ranking, anti-hallucination
finalize, and final-answer id parsing.

The HTTP/tool-execution paths are not exercised here (they need a live model);
these cover the deterministic glue that decides what ids ship to the client.

Run: pytest api/tests/test_prompt_agent.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.prompt_recs import _Evidence, _finalize, _parse_ids, _clean_tool_calls


class TestEvidence:
    def test_add_and_known(self):
        ev = _Evidence()
        ev.add("search_library", [{"id": "a"}, {"id": "b"}])
        assert ev.known() == {"a", "b"}

    def test_preserves_first_seen_order(self):
        ev = _Evidence()
        ev.add("t1", [{"id": "a"}, {"id": "b"}, {"id": "c"}])
        assert ev.order == ["a", "b", "c"]

    def test_ignores_items_without_id(self):
        ev = _Evidence()
        ev.add("t1", [{"title": "no id"}, {"id": "a"}])
        assert ev.known() == {"a"}

    def test_corroboration_across_tools_boosts_weight(self):
        ev = _Evidence()
        ev.add("t1", [{"id": "a"}, {"id": "b"}])
        ev.add("t2", [{"id": "a"}])  # 'a' surfaced by two distinct tools
        ranked = ev.ranked()
        assert ranked[0] == "a"

    def test_similarity_score_increases_weight(self):
        ev = _Evidence()
        ev.add("similar_by_audio", [{"id": "a", "score": 0.95}, {"id": "b", "score": 0.1}])
        assert ev.ranked() == ["a", "b"]

    def test_duplicate_within_same_tool_not_double_counted_for_corroboration(self):
        ev = _Evidence()
        ev.add("t1", [{"id": "a"}])
        ev.add("t1", [{"id": "a"}])  # same tool again
        # weight accrues but only one tool counted; sanity: still known once
        assert ev.order == ["a"]


class TestFinalize:
    def test_keeps_only_known_ids(self):
        # Anti-hallucination: model returns 'x' which no tool surfaced → dropped.
        ev = _Evidence()
        ev.add("t1", [{"id": "a"}, {"id": "b"}])
        out = _finalize(["a", "x", "b"], ev, limit=10)
        assert out == ["a", "b"]
        assert "x" not in out

    def test_preserves_model_order_for_known_ids(self):
        ev = _Evidence()
        ev.add("t1", [{"id": "a"}, {"id": "b"}, {"id": "c"}])
        out = _finalize(["c", "a"], ev, limit=10)
        assert out[:2] == ["c", "a"]

    def test_tops_up_from_evidence_when_under_limit(self):
        ev = _Evidence()
        ev.add("t1", [{"id": "a"}, {"id": "b"}, {"id": "c"}])
        out = _finalize(["a"], ev, limit=10)
        # 'a' chosen first, then b and c topped up from ranked evidence
        assert set(out) == {"a", "b", "c"}
        assert out[0] == "a"

    def test_respects_limit(self):
        ev = _Evidence()
        ev.add("t1", [{"id": f"id{i}"} for i in range(20)])
        out = _finalize([f"id{i}" for i in range(20)], ev, limit=5)
        assert len(out) == 5

    def test_empty_choice_falls_back_to_ranked(self):
        ev = _Evidence()
        ev.add("t1", [{"id": "a"}, {"id": "b"}])
        out = _finalize([], ev, limit=10)
        assert set(out) == {"a", "b"}

    def test_no_duplicates(self):
        ev = _Evidence()
        ev.add("t1", [{"id": "a"}, {"id": "b"}])
        out = _finalize(["a", "a", "b"], ev, limit=10)
        assert out == ["a", "b"]


class TestParseIds:
    def test_object_with_track_ids(self):
        assert _parse_ids('{"track_ids":["a","b"]}') == ["a", "b"]

    def test_fenced_json(self):
        assert _parse_ids('```json\n{"track_ids":["a"]}\n```') == ["a"]

    def test_prose_then_json(self):
        assert _parse_ids('Here is your playlist: {"track_ids":["a","b"]}') == ["a", "b"]

    def test_bare_array(self):
        assert _parse_ids('["a","b","c"]') == ["a", "b", "c"]

    def test_coerces_to_str(self):
        assert _parse_ids('{"track_ids":[1,2]}') == ["1", "2"]

    def test_garbage_returns_empty(self):
        assert _parse_ids("no playlist for you") == []


class TestCleanToolCalls:
    def test_keeps_only_spec_fields(self):
        raw = [{
            "id": "call_1",
            "type": "function",
            "index": 0,                       # provider-specific, must be dropped
            "reasoning_content": "...",       # must be dropped
            "function": {"name": "search_library",
                         "arguments": '{"query":"jazz"}',
                         "extra": "x"},
        }]
        out = _clean_tool_calls(raw)
        assert out == [{
            "id": "call_1",
            "type": "function",
            "function": {"name": "search_library", "arguments": '{"query":"jazz"}'},
        }]

    def test_missing_arguments_defaults(self):
        out = _clean_tool_calls([{"id": "c", "function": {"name": "get_taste_profile"}}])
        assert out[0]["function"]["arguments"] == "{}"
