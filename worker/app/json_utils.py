"""Robust JSON extraction from LLM responses.

LLMs frequently return JSON wrapped in markdown fences, prefixed with prose,
or truncated mid-object when they hit the token cap. This module centralises
the parsing so every call site (vibe tags, spectrogram analysis, the prompt
agent) gets the same battle-tested behaviour instead of ad-hoc regex salvage.

Public API:
  extract_json(content)            -> dict | list | None
  extract_json_object(content)     -> dict | None
  extract_json_array(content)      -> list | None
  extract_string_list(content, key)-> list[str]   (tolerates truncation)

The functions never raise — they return None / [] on failure so callers can
fall back gracefully. The truncation-tolerant array reader is the key fix for
the old regex that captured JSON *keys* (e.g. "tags") as if they were values.
"""
from __future__ import annotations

import json
import re
from typing import Any


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _strip_fences(content: str) -> str:
    """Pull the body out of a ```json ... ``` markdown fence if present."""
    m = _FENCE_RE.search(content)
    return m.group(1) if m else content


def _find_balanced(content: str, open_ch: str, close_ch: str) -> str | None:
    """Return the first balanced {...} / [...] span, respecting strings.

    Walks the text tracking string state and escape characters so that braces
    inside string literals don't throw off the depth counter. Returns the span
    including the delimiters, or None if no opening delimiter is found.
    """
    start = content.find(open_ch)
    if start < 0:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(content)):
        ch = content[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return content[start : i + 1]
    return None  # unbalanced (truncated)


def extract_json(content: str | None) -> Any | None:
    """Best-effort parse of a JSON value from arbitrary LLM text.

    Tries, in order: direct parse, fence-stripped parse, first balanced object,
    first balanced array. Returns the decoded value or None.
    """
    if not content:
        return None
    candidates: list[str] = []
    stripped = _strip_fences(content).strip()
    candidates.append(stripped)

    obj_span = _find_balanced(stripped, "{", "}")
    if obj_span:
        candidates.append(obj_span)
    arr_span = _find_balanced(stripped, "[", "]")
    if arr_span:
        candidates.append(arr_span)

    for cand in candidates:
        try:
            return json.loads(cand)
        except (json.JSONDecodeError, ValueError):
            continue
    return None


def extract_json_object(content: str | None) -> dict | None:
    """Parse and return a JSON object (dict), or None."""
    val = extract_json(content)
    return val if isinstance(val, dict) else None


def extract_json_array(content: str | None) -> list | None:
    """Parse and return a JSON array (list), or None."""
    val = extract_json(content)
    return val if isinstance(val, list) else None


# Matches a JSON string literal value, handling escaped chars.
_STR_LITERAL_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')


def extract_string_list(content: str | None, key: str | None = None) -> list[str]:
    """Extract a list of strings, tolerating truncated JSON.

    Strategy:
      1. Try a clean parse. If it yields a list, return its string items.
         If it yields an object and `key` is given, return that key's list.
      2. On failure (truncation), locate the array that follows `"key":` (or the
         first '[' if no key) and read complete string literals out of it,
         stopping at the first incomplete literal. This deliberately reads only
         the *values* inside the array span, so a JSON key like "tags" can never
         leak into the result — the old bug.

    Always returns a list (possibly empty); never raises.
    """
    if not content:
        return []

    # Fast path — clean parse.
    parsed = extract_json(content)
    if isinstance(parsed, list):
        return [str(x) for x in parsed if isinstance(x, (str, int, float))]
    if isinstance(parsed, dict) and key and isinstance(parsed.get(key), list):
        return [str(x) for x in parsed[key] if isinstance(x, (str, int, float))]

    # Salvage path — truncated array. Find where the target array starts.
    text = _strip_fences(content)
    arr_start = -1
    if key:
        key_pat = re.compile(rf'"{re.escape(key)}"\s*:\s*\[')
        m = key_pat.search(text)
        if m:
            arr_start = m.end() - 1  # position of '['
    if arr_start < 0:
        arr_start = text.find("[")
    if arr_start < 0:
        return []

    # Read string literals only within the array region (up to a closing ']'
    # if present, else to end of string for the truncated case).
    arr_end = text.find("]", arr_start)
    region = text[arr_start : arr_end if arr_end >= 0 else len(text)]
    return [m.group(1) for m in _STR_LITERAL_RE.finditer(region)]
