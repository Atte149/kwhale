"""Robust JSON extraction from LLM responses (API-side copy).

Mirrors worker/app/json_utils.py. The two services are packaged separately
(no shared import path), so the logic is duplicated deliberately. Keep them in
sync if you change the parsing behaviour.

Public API:
  extract_json(content)            -> dict | list | None
  extract_json_object(content)     -> dict | None
  extract_json_array(content)      -> list | None
  extract_string_list(content,key) -> list[str]   (tolerates truncation)
"""
from __future__ import annotations

import json
import re
from typing import Any


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _strip_fences(content: str) -> str:
    m = _FENCE_RE.search(content)
    return m.group(1) if m else content


def _find_balanced(content: str, open_ch: str, close_ch: str) -> str | None:
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
    return None


def extract_json(content: str | None) -> Any | None:
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
    val = extract_json(content)
    return val if isinstance(val, dict) else None


def extract_json_array(content: str | None) -> list | None:
    val = extract_json(content)
    return val if isinstance(val, list) else None


_STR_LITERAL_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')


def extract_string_list(content: str | None, key: str | None = None) -> list[str]:
    if not content:
        return []
    parsed = extract_json(content)
    if isinstance(parsed, list):
        return [str(x) for x in parsed if isinstance(x, (str, int, float))]
    if isinstance(parsed, dict) and key and isinstance(parsed.get(key), list):
        return [str(x) for x in parsed[key] if isinstance(x, (str, int, float))]

    text = _strip_fences(content)
    arr_start = -1
    if key:
        key_pat = re.compile(rf'"{re.escape(key)}"\s*:\s*\[')
        m = key_pat.search(text)
        if m:
            arr_start = m.end() - 1
    if arr_start < 0:
        arr_start = text.find("[")
    if arr_start < 0:
        return []
    arr_end = text.find("]", arr_start)
    region = text[arr_start : arr_end if arr_end >= 0 else len(text)]
    return [m.group(1) for m in _STR_LITERAL_RE.finditer(region)]
