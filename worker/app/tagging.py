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

# ── Collaborative artist parsing ────────────────────────────────────────────
#
# The VorBis `artists` tag (multi-value, ';'-separated) is the source of
# truth for "who is on this track". It is widely written by Picard, beets
# and friends. Some libraries only set `artist` + a "(feat. X)" in the
# title, so we also fall back to extracting features from the title.
#
# The output is a de-duplicated list of credited artists, primary first,
# never empty when we know who the main artist is.

_FEAT_RE = re.compile(
    r"""
    [\(\[]\s*                   # opening bracket
    (?:feat\.?|ft\.?|featuring) # feat / ft / featuring
    \s+
    ([^\)\]]+?)                 # captured list of featured artists
    \s*[\)\]]                   # closing bracket
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _split_artists_tag(raw: str | list[str] | None) -> list[str]:
    """Split a VorBis `artists` value (or list) into a clean list.

    Accepts both a single ';'-separated string ("A; B; C", as written by
    Picard/beets) and a list of strings (mutagen's easy=True form, one
    value per written tag). Whitespace, case, and duplicates are folded.
    """
    if not raw:
        return []
    if isinstance(raw, str):
        parts = raw.split(";")
    else:
        # list[str] — flatten with "; " if a single string contains separators.
        parts = []
        for v in raw:
            if not v:
                continue
            parts.extend(p for p in v.split(";") if p)
    out: list[str] = []
    seen: set[str] = set()
    for p in parts:
        name = p.strip()
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


def _split_feat_list(payload: str) -> list[str]:
    """Split the inside of a (feat. X & Y, Z) block into clean artist names.

    Accepts '&', ',' and 'and' (with optional whitespace) as separators.
    """
    if not payload:
        return []
    # Normalise "and" to '&' first; then split on either separator.
    parts = re.split(r"\s*(?:,|\band\b|&)\s*", payload, flags=re.IGNORECASE)
    return [p.strip() for p in parts if p and p.strip()]


def _add_unique(target: list[str], seen: set[str], name: str) -> None:
    name = (name or "").strip()
    if not name:
        return
    key = name.casefold()
    if key in seen:
        return
    seen.add(key)
    target.append(name)


def extract_all_artists(
    artists_tag: str | list[str] | None,
    artist_tag: str | list[str] | None,
    title: str | list[str] | None = None,
) -> list[str]:
    """Return every credited artist on a track, primary first, deduped.

    Resolution order:
      1. VorBis `artists` tag (multi-value, ';' separated). This is what
         Picard, beets, and our own tagger write for collaboration tracks.
         When present, it is the source of truth — we do NOT also parse
         the title, which would risk duplicates and false positives on
         songs whose title incidentally contains the word "feat".
      2. `artist` tag (single) — guarantees we never return an empty list
         when we know the main artist.
      3. (feat. ...) / (ft. ...) blocks in the title — only when the
         `artists` tag is missing, since older libraries embed the
         collaboration metadata in the title.

    Returns an empty list only when there is genuinely no artist info.
    """
    out: list[str] = []
    seen: set[str] = set()

    for name in _split_artists_tag(artists_tag):
        _add_unique(out, seen, name)

    if not out:
        for name in _split_artists_tag(artist_tag):
            _add_unique(out, seen, name)

        if title:
            titles = title if isinstance(title, list) else [title]
            for t in titles:
                if not t:
                    continue
                for m in _FEAT_RE.finditer(t):
                    for name in _split_feat_list(m.group(1)):
                        _add_unique(out, seen, name)
                if out:
                    break

    return out


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
