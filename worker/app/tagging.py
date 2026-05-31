"""Pure tag/description parsing for LLM audio analysis.

Extracted from indexer.py so it can be unit-tested without pulling in psycopg2
/ essentia (the indexer's heavy deps). Depends only on json_utils, which is
pure-Python. indexer.py imports from here.
"""
from __future__ import annotations

import re

from .json_utils import extract_json_object, extract_string_list

# Tags that are JSON structure or generic noise, never real descriptors.
_TAG_STOPWORDS = {"tags", "desc", "description", "mood", "atmosphere", "feel", "null", "none", ""}
_MAX_TAGS = 8
_MAX_TAG_LEN = 40

_DESC_RE = re.compile(r'"desc"\s*:\s*"((?:[^"\\]|\\.)*)"')


def clean_tags(raw: list) -> list[str]:
    """Normalise, de-noise and de-duplicate LLM-produced tags.

    Drops JSON keys/stopwords, numeric-only fragments, over-long strings and
    duplicates while preserving order. This is the safety net that keeps junk
    like "tags"/"confidence"/"0.9" (from truncated JSON) out of the database.
    """
    out: list[str] = []
    seen: set[str] = set()
    for t in raw:
        tag = str(t).strip().strip('"\'').lower()
        if not tag or tag in _TAG_STOPWORDS:
            continue
        if len(tag) > _MAX_TAG_LEN:
            continue
        # Reject purely numeric / punctuation fragments (e.g. "0.9", ":").
        if not any(c.isalpha() for c in tag):
            continue
        if tag in seen:
            continue
        seen.add(tag)
        out.append(tag)
        if len(out) >= _MAX_TAGS:
            break
    return out


def parse_spectro(content: str) -> dict:
    """Parse {desc,tags} from the omni reply, tolerating truncated JSON.

    Uses json_utils for a clean object parse when possible, otherwise a
    truncation-tolerant array read for the tags. The tag list is always run
    through clean_tags so structural noise never reaches the DB.
    """
    if not content:
        return {"desc": None, "tags": []}

    obj = extract_json_object(content)
    if obj is not None:
        desc = obj.get("desc")
        tags = obj.get("tags") if isinstance(obj.get("tags"), list) else []
        return {"desc": (str(desc) if desc else None), "tags": clean_tags(tags)}

    # Truncated / malformed — salvage desc and tags separately.
    desc_m = _DESC_RE.search(content)
    desc = desc_m.group(1) if desc_m else None
    tags = extract_string_list(content, key="tags")
    return {"desc": desc, "tags": clean_tags(tags)}
