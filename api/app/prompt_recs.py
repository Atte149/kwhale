"""Real tool-using agent for prompt → playlist.

A free-text prompt is handed to the LLM with a set of function tools. The model
decides which tools to call, we execute them against the library/DB, feed
results back, and loop until it returns a final playlist.

Tools available to the agent:
  - search_library(query)          : metadata search via Navidrome
  - similar_by_audio(track_id)     : pgvector nearest on features_vector
  - semantic_search(text)          : pgvector nearest on lyrics_embedding (bge-m3)
  - filter_by_features(...)        : energy/valence/tempo bands on track_features
  - filter_by_vibe_tags(tags)      : match vibe_tags + spectro_tags
  - get_taste_profile()            : user's averaged preferences

Hardening (vs. the original):
  * every id the agent returns is validated against the set of ids the tools
    actually surfaced — the model cannot hallucinate library ids;
  * each tool result is also scored (how many times an id was surfaced and by
    how many distinct tools) so we can rank the final list sensibly;
  * JSON is parsed with the shared robust extractor, not bare json.loads;
  * tool dispatch is fully guarded — a single failing tool degrades to an
    error payload fed back to the model rather than aborting the run;
  * falls back to a keyword heuristic if the LLM/tooling is unavailable.
"""
import json
import httpx

from .config import settings
from . import navidrome
from .json_utils import extract_json_object, extract_string_list

MAX_ROUNDS = 6
# Per-id evidence accumulates across tool calls; used to rank the final list.
_AUDIO_SCORE_BONUS = 0.5  # extra weight when a tool reported a similarity score


# ── Tool implementations ────────────────────────────────────────────────────

async def _t_search_library(pool, query: str, limit: int = 25) -> list[dict]:
    res = await navidrome.search(query, song_count=limit)
    songs = res.get("song", [])
    return [{"id": s["id"], "title": s.get("title"), "artist": s.get("artist")} for s in songs]


async def _t_similar_by_audio(pool, track_id: str, limit: int = 25) -> list[dict]:
    base = await pool.fetchrow(
        "SELECT features_vector FROM track_features WHERE navidrome_id=$1", track_id
    )
    if not base or base["features_vector"] is None:
        return []
    rows = await pool.fetch(
        "SELECT navidrome_id, title, artist, "
        "1 - (features_vector <=> $1::vector) AS score "
        "FROM track_features WHERE navidrome_id != $2 AND features_vector IS NOT NULL "
        "ORDER BY features_vector <=> $1::vector LIMIT $3",
        base["features_vector"], track_id, limit,
    )
    return [{"id": r["navidrome_id"], "title": r["title"], "artist": r["artist"],
             "score": round(float(r["score"]), 3)} for r in rows]


async def _embed_query(text: str) -> list[float] | None:
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(settings.embedding_api_url,
                                  json={"model": "bge-m3", "input": text[:4000]})
            r.raise_for_status()
            return r.json()["data"][0]["embedding"]
    except Exception as e:
        print(f"[agent] embed_query failed: {e}")
        return None


async def _t_semantic_search(pool, text: str, limit: int = 25) -> list[dict]:
    emb = await _embed_query(text)
    if not emb:
        return []
    vec = "[" + ",".join(str(x) for x in emb) + "]"
    rows = await pool.fetch(
        "SELECT navidrome_id, title, artist, "
        "1 - (lyrics_embedding <=> $1::vector) AS score "
        "FROM track_features WHERE lyrics_embedding IS NOT NULL "
        "ORDER BY lyrics_embedding <=> $1::vector LIMIT $2",
        vec, limit,
    )
    return [{"id": r["navidrome_id"], "title": r["title"], "artist": r["artist"],
             "score": round(float(r["score"]), 3)} for r in rows]


async def _t_filter_by_features(pool, energy_min=None, energy_max=None,
                                valence_min=None, valence_max=None,
                                bpm_min=None, bpm_max=None, limit: int = 30) -> list[dict]:
    conds, args = ["features_vector IS NOT NULL"], []
    def add(col, lo, hi):
        nonlocal args
        if lo is not None:
            args.append(lo); conds.append(f"{col} >= ${len(args)}")
        if hi is not None:
            args.append(hi); conds.append(f"{col} <= ${len(args)}")
    add("energy", energy_min, energy_max)
    add("valence", valence_min, valence_max)
    add("bpm", bpm_min, bpm_max)
    args.append(limit)
    rows = await pool.fetch(
        f"SELECT navidrome_id, title, artist FROM track_features "
        f"WHERE {' AND '.join(conds)} ORDER BY RANDOM() LIMIT ${len(args)}", *args
    )
    return [{"id": r["navidrome_id"], "title": r["title"], "artist": r["artist"]} for r in rows]


async def _t_filter_by_vibe_tags(pool, tags: list[str], limit: int = 30) -> list[dict]:
    if not tags:
        return []
    rows = await pool.fetch(
        "SELECT navidrome_id, title, artist, vibe_tags, spectro_tags FROM track_features "
        "WHERE vibe_tags IS NOT NULL OR spectro_tags IS NOT NULL"
    )
    low = [t.lower() for t in tags]
    scored = []
    for r in rows:
        tagset = []
        for col in ("vibe_tags", "spectro_tags"):
            v = r[col]
            if isinstance(v, str):
                try: v = json.loads(v)
                except Exception: v = []
            tagset += [str(x).lower() for x in (v or [])]
        score = sum(1 for k in low if any(k in t or t in k for t in tagset))
        if score:
            scored.append((score, r))
    scored.sort(key=lambda x: -x[0])
    return [{"id": r["navidrome_id"], "title": r["title"], "artist": r["artist"]}
            for _, r in scored[:limit]]


async def _t_get_taste_profile(pool, user_id: str = "default") -> dict:
    row = await pool.fetchrow("SELECT * FROM taste_profile WHERE user_id=$1", user_id)
    return dict(row) if row else {"note": "no profile yet"}


TOOLS_SPEC = [
    {"type": "function", "function": {"name": "search_library",
        "description": "Поиск треков в библиотеке по тексту (исполнитель/название/альбом).",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "similar_by_audio",
        "description": "Найти треки, похожие по ЗВУЧАНИЮ на заданный track_id (аудио-вектор).",
        "parameters": {"type": "object", "properties": {"track_id": {"type": "string"}}, "required": ["track_id"]}}},
    {"type": "function", "function": {"name": "semantic_search",
        "description": "Семантический поиск по СМЫСЛУ текстов песен (эмбеддинги). Дай описание темы/настроения словами.",
        "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}},
    {"type": "function", "function": {"name": "filter_by_features",
        "description": "Фильтр по аудио-фичам: energy/valence 0..1, bpm. Любые границы опциональны.",
        "parameters": {"type": "object", "properties": {
            "energy_min": {"type": "number"}, "energy_max": {"type": "number"},
            "valence_min": {"type": "number"}, "valence_max": {"type": "number"},
            "bpm_min": {"type": "number"}, "bpm_max": {"type": "number"}}}}},
    {"type": "function", "function": {"name": "filter_by_vibe_tags",
        "description": "Найти треки по vibe/спектро-тегам (напр. ['меланхоличный','плотный бас']).",
        "parameters": {"type": "object", "properties": {
            "tags": {"type": "array", "items": {"type": "string"}}}, "required": ["tags"]}}},
    {"type": "function", "function": {"name": "get_taste_profile",
        "description": "Профиль вкуса пользователя (средние предпочтения).",
        "parameters": {"type": "object", "properties": {}}}},
]

_DISPATCH = {
    "search_library": _t_search_library,
    "similar_by_audio": _t_similar_by_audio,
    "semantic_search": _t_semantic_search,
    "filter_by_features": _t_filter_by_features,
    "filter_by_vibe_tags": _t_filter_by_vibe_tags,
    "get_taste_profile": _t_get_taste_profile,
}


SYSTEM = (
    "Ты — музыкальный куратор сервиса KWhale. По запросу пользователя собери лучший плейлист "
    "ИЗ ЕГО БИБЛИОТЕКИ, используя инструменты. У тебя есть поиск по метаданным, по звучанию "
    "(аудио-вектор), по смыслу текстов, по аудио-фичам (energy/valence/bpm) и по vibe/спектро-тегам, "
    "а также профиль вкуса. Комбинируй НЕСКОЛЬКО инструментов для точности. "
    "Когда готов — верни ТОЛЬКО JSON: {\"track_ids\": [\"id1\",\"id2\", ...]} с 15-40 id из результатов инструментов. "
    "Не выдумывай id — бери их строго из ответов инструментов."
)


class _Evidence:
    """Accumulates the ids that tools surfaced and how strongly.

    `order` preserves first-seen order; `weight` aggregates a relevance signal
    (a hit is worth 1.0, plus the tool's own similarity score when present, plus
    a small bonus per *distinct* tool that surfaced the id). This lets us rank
    the final playlist even when the model just says "use what you found".
    """

    def __init__(self) -> None:
        self.order: list[str] = []
        self.weight: dict[str, float] = {}
        self._tools_for: dict[str, set[str]] = {}

    def add(self, tool: str, items: list[dict]) -> None:
        for it in items:
            tid = it.get("id")
            if not tid:
                continue
            tid = str(tid)
            if tid not in self.weight:
                self.order.append(tid)
                self.weight[tid] = 0.0
                self._tools_for[tid] = set()
            base = 1.0
            score = it.get("score")
            if isinstance(score, (int, float)):
                base += _AUDIO_SCORE_BONUS * float(score)
            # Reward corroboration across distinct tools.
            if tool not in self._tools_for[tid]:
                self._tools_for[tid].add(tool)
                base += 0.25 * (len(self._tools_for[tid]) - 1)
            self.weight[tid] += base

    def known(self) -> set[str]:
        return set(self.weight)

    def ranked(self) -> list[str]:
        prior = {tid: i for i, tid in enumerate(self.order)}
        return sorted(self.order, key=lambda t: (-self.weight[t], prior[t]))


def _clean_tool_calls(tcalls: list[dict]) -> list[dict]:
    """Re-emit a minimal, spec-compliant tool_calls array.

    Some gateways 500 when the assistant turn echoes back provider-specific
    fields (reasoning_content, tool_calls[].index). We keep only id/type/function.
    """
    clean = []
    for c in tcalls:
        fn = c.get("function") or {}
        clean.append({
            "id": c.get("id"),
            "type": "function",
            "function": {"name": fn.get("name"),
                         "arguments": fn.get("arguments", "{}")},
        })
    return clean


async def run_prompt_agent(pool, prompt: str, limit: int = 30) -> list[str]:
    if not settings.openai_api_key:
        return await _fallback(pool, prompt, limit)

    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": prompt},
    ]
    evidence = _Evidence()

    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
        "User-Agent": "kwhale/1.0",
    }

    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            for _ in range(MAX_ROUNDS):
                msg = await _chat_once(client, headers, messages)
                tcalls = msg.get("tool_calls")

                if not tcalls:
                    # Final answer — parse and validate the ids the model chose.
                    content = msg.get("content") or ""
                    chosen = _parse_ids(content)
                    return _finalize(chosen, evidence, limit)

                messages.append({"role": "assistant",
                                 "content": msg.get("content") or "",
                                 "tool_calls": _clean_tool_calls(tcalls)})
                for call in tcalls:
                    result = await _run_tool(pool, call)
                    if isinstance(result, list):
                        evidence.add(call.get("function", {}).get("name", "?"), result)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.get("id"),
                        "content": json.dumps(result, ensure_ascii=False)[:4000],
                    })
        # Ran out of rounds — return the best-ranked evidence.
        ranked = evidence.ranked()
        return ranked[:limit] if ranked else await _fallback(pool, prompt, limit)
    except httpx.HTTPStatusError as e:
        body = ""
        try:
            body = e.response.text[:500]
        except Exception:
            pass
        print(f"[agent] HTTP {e.response.status_code}: {body}")
        return await _fallback(pool, prompt, limit)
    except Exception as e:
        print(f"[agent] run failed: {e}")
        return await _fallback(pool, prompt, limit)


async def _chat_once(client: httpx.AsyncClient, headers: dict, messages: list[dict]) -> dict:
    """One chat/completions round; returns choices[0].message. Raises on HTTP error."""
    r = await client.post(
        f"{settings.openai_api_base}/chat/completions",
        headers=headers,
        json={"model": settings.llm_model, "messages": messages,
              "tools": TOOLS_SPEC, "tool_choice": "auto", "max_tokens": 800},
    )
    r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"]


async def _run_tool(pool, call: dict) -> object:
    """Dispatch a single tool call, never raising — failures become payloads
    the model can read and react to."""
    fn = (call.get("function") or {}).get("name")
    impl = _DISPATCH.get(fn)
    if impl is None:
        return {"error": f"unknown tool: {fn}"}
    try:
        args = json.loads((call.get("function") or {}).get("arguments") or "{}")
        if not isinstance(args, dict):
            args = {}
    except Exception:
        args = {}
    try:
        return await impl(pool, **args)
    except TypeError as e:
        # Bad/extra arguments from the model — report instead of crashing.
        return {"error": f"bad arguments for {fn}: {e}"}
    except Exception as e:
        print(f"[agent] tool {fn} failed: {e}")
        return {"error": f"{fn} failed: {e}"}


def _finalize(chosen_ids: list[str], evidence: _Evidence, limit: int) -> list[str]:
    """Keep only model-chosen ids that tools actually surfaced (anti-hallucination),
    preserving the model's order; then top up with ranked evidence to fill `limit`."""
    known = evidence.known()
    out: list[str] = []
    seen: set[str] = set()
    for tid in chosen_ids:
        if tid in known and tid not in seen:
            seen.add(tid)
            out.append(tid)
    if len(out) < limit:
        for tid in evidence.ranked():
            if tid not in seen:
                seen.add(tid)
                out.append(tid)
            if len(out) >= limit:
                break
    return out[:limit]


def _parse_ids(content: str) -> list[str]:
    """Extract track_ids from the model's final JSON, robust to fences/prose."""
    obj = extract_json_object(content)
    if obj and isinstance(obj.get("track_ids"), list):
        return [str(x) for x in obj["track_ids"]]
    # Some models emit a bare array.
    return extract_string_list(content)


async def _fallback(pool, prompt: str, limit: int) -> list[str]:
    """No-LLM keyword heuristic over vibe/spectro tags + random."""
    words = [w.strip(".,!?").lower() for w in prompt.split()]
    ids = await _t_filter_by_vibe_tags(pool, words, limit)
    out = [x["id"] for x in ids]
    if len(out) < limit:
        extra = await navidrome.get_random_songs(size=limit - len(out))
        out += [s["id"] for s in extra]
    return out[:limit]
