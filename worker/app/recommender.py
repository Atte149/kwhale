"""Recommendation engine v2 — content retrieval (pgvector) + transparent
multi-layer scoring + optional LLM curation.

No ALS / collaborative filtering: this is a personal (largely single-user) server,
so there is no cross-user signal to learn — the old `implicit` ALS path was both
meaningless here and fragile (API drift). Everything below is plain SQL + a
weighted sum + one LLM call, so a small model can read and maintain it.

generate_recommendations(user_id, algorithm):
  1. seed     — the user's positively-engaged tracks (completion, repeats, stars)
  2. retrieve — pgvector cosine kNN from the seed centroid (discovery candidates)
  3. score    — weighted sum: content similarity + personal behaviour + novelty
  4. curate   — one LLM call (opencode go) orders the shortlist; falls back to the
                score order if the LLM is unavailable
  5. persist  — always write a non-empty set when the library has candidates

The previous version returned [] (and persisted nothing) whenever the loved-set
was empty, which is why the `recommendations` table stayed empty. This version
always falls back to cold-start picks so the table is populated.
"""
import os
import re
import json
from datetime import date

import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ.get("DATABASE_URL", "")

# LLM curation (opencode go — see worker/.env OPENAI_API_BASE/KEY)
RECS_LLM_MODEL = os.environ.get("RECS_LLM_MODEL", "minimax-m3")
RECS_LLM_ENABLED = os.environ.get("RECS_LLM_ENABLED", "1") != "0"

# ── Scoring weights (transparent, tunable constants — no trained model) ──────
W_CONTENT = 0.50   # audio similarity to the taste centroid (pgvector)
W_PERSONAL = 0.35  # behaviour: fav/freq/completion/time/recency − skip (personal_score)
W_NOVELTY = 0.15   # boost artists not already saturated in the seed

CANDIDATE_POOL = 120   # pgvector kNN pool
LLM_SHORTLIST = 60     # candidates handed to the LLM
FINAL_N = 30           # stored recommendations


def _get_conn():
    return psycopg2.connect(DATABASE_URL)


def _r(x):
    try:
        return round(float(x), 2)
    except (TypeError, ValueError):
        return None


# ── 1. Seed ──────────────────────────────────────────────────────────────────
def _seed_tracks(user_id: str, days: int = 90, limit: int = 60) -> list[str]:
    """Tracks the user positively engaged with, best first.

    Engagement = plays weighted by completion; tracks dominated by skips drop out.
    """
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT navidrome_id,
                       COUNT(*) FILTER (WHERE event_type IN ('play','complete')) AS plays,
                       COUNT(*) FILTER (WHERE skipped)                           AS skips
                FROM playback_events
                WHERE user_id = %s
                  AND navidrome_id IS NOT NULL
                  AND ts >= NOW() - make_interval(days => %s)
                GROUP BY navidrome_id
                HAVING COUNT(*) FILTER (WHERE event_type IN ('play','complete')) >= 1
                ORDER BY (COUNT(*) FILTER (WHERE event_type IN ('play','complete'))
                          * (0.5 + COALESCE(AVG(completion_pct), 0))) DESC
                LIMIT %s
                """,
                (user_id, days, limit),
            )
            rows = cur.fetchall()
    # keep tracks that were played more than they were skipped
    return [r[0] for r in rows if (r[1] or 0) >= (r[2] or 0)]


# ── 2. Candidate retrieval ───────────────────────────────────────────────────
def _retrieve_candidates(seed_ids: list[str], exclude_ids: set[str],
                         n: int = CANDIDATE_POOL) -> list[tuple[str, float]]:
    """pgvector cosine kNN from the centroid of the seed tracks' feature vectors.

    Returns [(navidrome_id, similarity)] with similarity in roughly -1..1.
    Falls back to [] (caller switches to cold-start) on any error — e.g. if the
    pgvector build lacks AVG(vector).
    """
    if not seed_ids:
        return []
    excl = list((set(exclude_ids) | set(seed_ids)) or {""})
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    WITH centroid AS (
                        SELECT AVG(features_vector) AS v
                        FROM track_features
                        WHERE navidrome_id = ANY(%s)
                          AND features_vector IS NOT NULL
                    )
                    SELECT tf.navidrome_id,
                           1 - (tf.features_vector <=> (SELECT v FROM centroid)) AS sim
                    FROM track_features tf
                    WHERE tf.features_vector IS NOT NULL
                      AND (SELECT v FROM centroid) IS NOT NULL
                      AND tf.navidrome_id <> ALL(%s)
                      AND tf.index_status = 'ok'
                    ORDER BY tf.features_vector <=> (SELECT v FROM centroid)
                    LIMIT %s
                    """,
                    (seed_ids, excl, n),
                )
                return [(row[0], float(row[1])) for row in cur.fetchall()]
    except Exception as e:
        print(f"[recs] candidate retrieval failed: {e}")
        return []


def _coldstart_candidates(exclude_ids: set[str],
                          n: int = CANDIDATE_POOL) -> list[tuple[str, float]]:
    """No usable history yet — surface the most recently indexed tracks."""
    excl = list(set(exclude_ids) or {""})
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT navidrome_id
                FROM track_features
                WHERE index_status = 'ok'
                  AND navidrome_id <> ALL(%s)
                ORDER BY indexed_at DESC NULLS LAST
                LIMIT %s
                """,
                (excl, n),
            )
            return [(row[0], 0.0) for row in cur.fetchall()]


def _recent_recommended(user_id: str, days: int = 7) -> set[str]:
    """Track ids recommended in the last `days` — anti-repeat."""
    out: set[str] = set()
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT track_ids FROM recommendations "
                "WHERE user_id = %s AND date >= CURRENT_DATE - make_interval(days => %s)",
                (user_id, days),
            )
            for (tids,) in cur.fetchall():
                if tids:
                    out.update(tids)
    return out


def _meta_for(ids: list[str]) -> dict[str, dict]:
    """navidrome_id -> {artist,title,vibe_tags,bpm,energy,valence} for scoring + LLM."""
    if not ids:
        return {}
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT navidrome_id, artist, title, vibe_tags, bpm, energy, valence "
                "FROM track_features WHERE navidrome_id = ANY(%s)",
                (ids,),
            )
            return {
                row[0]: {"artist": row[1], "title": row[2], "vibe_tags": row[3],
                         "bpm": row[4], "energy": row[5], "valence": row[6]}
                for row in cur.fetchall()
            }


# ── 3. Scoring ───────────────────────────────────────────────────────────────
def _score(user_id: str, candidates: list[tuple[str, float]],
           seed_ids: list[str], meta: dict[str, dict]) -> dict[str, float]:
    from .personal_score import compute_personal_scores

    cand_ids = [c for c, _ in candidates]
    sim = {c: s for c, s in candidates}
    try:
        personal = compute_personal_scores(user_id, cand_ids)
    except Exception as e:
        print(f"[recs] personal_score failed: {e}")
        personal = {}

    seed_artists: dict[str, int] = {}
    for s in seed_ids:
        a = (meta.get(s) or {}).get("artist")
        if a:
            seed_artists[a] = seed_artists.get(a, 0) + 1

    sims = [sim[c] for c in cand_ids] or [0.0]
    lo, hi = min(sims), max(sims)
    rng = (hi - lo) or 1.0

    out: dict[str, float] = {}
    for c in cand_ids:
        content = (sim[c] - lo) / rng                       # 0..1
        pers = personal.get(c, 0.0)                         # ~ -0.25..1
        artist = (meta.get(c) or {}).get("artist")
        novelty = 0.0 if (artist and artist in seed_artists) else 1.0
        out[c] = round(W_CONTENT * content + W_PERSONAL * pers + W_NOVELTY * novelty, 4)
    return out


# ── 4. LLM curation (one call, opencode go) ──────────────────────────────────
def _taste_summary(user_id: str) -> str:
    with _get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                "SELECT avg_bpm, avg_energy, avg_valence, preferred_hours "
                "FROM taste_profile WHERE user_id = %s",
                (user_id,),
            )
            r = cur.fetchone()
    if not r:
        return "нет данных (новый пользователь)"
    return (f"bpm≈{_r(r['avg_bpm'])}, energy≈{_r(r['avg_energy'])}, "
            f"valence≈{_r(r['avg_valence'])}, любимые часы={r['preferred_hours']}")


def _parse_int_array(s: str) -> list[int]:
    m = re.search(r"\[[\s\d,]*\]", s or "")
    if not m:
        return []
    try:
        return [int(x) for x in json.loads(m.group(0))]
    except Exception:
        return []


def _vibe_tags(raw) -> list[str]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            v = json.loads(raw)
            return v if isinstance(v, list) else []
        except Exception:
            return []
    return []


def _llm_curate(user_id: str, shortlist: list[str], meta: dict[str, dict],
                mode: str) -> list[str] | None:
    """One LLM call: order the shortlist by fit to the user's taste.

    The LLM only re-orders ids it was given (we map by index, not by free text),
    so it can't hallucinate tracks. Returns None on any failure → caller keeps
    the deterministic score order.
    """
    if not RECS_LLM_ENABLED or not shortlist:
        return None
    try:
        from .llm_client import chat_completion, LLMError
    except Exception:
        return None

    lines = []
    for i, tid in enumerate(shortlist):
        m = meta.get(tid, {})
        tags = _vibe_tags(m.get("vibe_tags"))
        lines.append(
            f"{i}. {m.get('artist','?')} — {m.get('title','?')} "
            f"[bpm={_r(m.get('bpm'))} energy={_r(m.get('energy'))} "
            f"valence={_r(m.get('valence'))} vibe={','.join(tags[:4])}]"
        )
    sys = (
        "Ты — музыкальный куратор. Дан профиль вкуса пользователя и пронумерованный "
        "список треков-кандидатов. Верни СТРОГО JSON-массив их номеров (целых чисел) "
        "в порядке убывания релевантности под вкус пользователя, максимум 30 штук, "
        "без текста и пояснений. Пример ответа: [3,0,12,7]"
    )
    usr = (f"Профиль вкуса: {_taste_summary(user_id)}\nРежим: {mode}\n"
           f"Кандидаты:\n" + "\n".join(lines))
    try:
        msg = chat_completion(
            [{"role": "system", "content": sys}, {"role": "user", "content": usr}],
            model=RECS_LLM_MODEL, max_tokens=400, temperature=0.4,
        )
        order = _parse_int_array(msg.get("content", ""))
        ordered = [shortlist[i] for i in order if 0 <= i < len(shortlist)]
        if not ordered:
            return None
        seen = set(ordered)
        ordered += [t for t in shortlist if t not in seen]  # keep the rest, score order
        return ordered
    except LLMError as e:
        print(f"[recs] LLM curation unavailable: {e}")
        return None
    except Exception as e:
        print(f"[recs] LLM curation error: {e}")
        return None


# ── 5. Persist ───────────────────────────────────────────────────────────────
def _persist(user_id: str, algorithm: str, final: list[str],
             final_scores: dict[str, float]) -> None:
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO recommendations (user_id, date, algorithm, track_ids, scores)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (user_id, date, algorithm) DO UPDATE
                SET track_ids = EXCLUDED.track_ids,
                    scores = EXCLUDED.scores,
                    generated_at = NOW()
                """,
                (user_id, date.today().isoformat(), algorithm,
                 json.dumps(final), json.dumps(final_scores)),
            )


def generate_recommendations(user_id: str, algorithm: str = "hybrid") -> list[str]:
    seed = _seed_tracks(user_id)
    recent = _recent_recommended(user_id)

    if seed:
        candidates = _retrieve_candidates(seed, exclude_ids=recent)
        if not candidates:
            candidates = _coldstart_candidates(exclude_ids=set(seed) | recent)
    else:
        candidates = _coldstart_candidates(exclude_ids=recent)

    if not candidates:
        return []

    cand_ids = [c for c, _ in candidates]
    meta = _meta_for(list(set(cand_ids) | set(seed)))
    scored = _score(user_id, candidates, seed, meta)
    shortlist = sorted(scored, key=lambda t: scored[t], reverse=True)[:LLM_SHORTLIST]

    ordered = _llm_curate(user_id, shortlist, meta, algorithm) or shortlist
    final = ordered[:FINAL_N]
    final_scores = {t: round(scored.get(t, 0.0), 4) for t in final}

    _persist(user_id, algorithm, final, final_scores)
    return final


def update_taste_profile(user_id: str) -> dict:
    """Materialise taste profile from playback_events into taste_profile table."""
    with _get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                """
                SELECT
                    AVG(tf.bpm) AS avg_bpm,
                    AVG(tf.energy) AS avg_energy,
                    AVG(tf.valence) AS avg_valence,
                    COUNT(*) FILTER (WHERE pe.completion_pct > 0.8 AND pe.event_type='complete')::float
                        / NULLIF(COUNT(*) FILTER (WHERE pe.event_type='play'), 0) AS completion_rate,
                    COUNT(*) FILTER (WHERE pe.skipped = TRUE)::float
                        / NULLIF(COUNT(*), 0) AS skip_rate
                FROM playback_events pe
                LEFT JOIN track_features tf ON tf.navidrome_id = pe.navidrome_id
                WHERE pe.user_id = %s
                  AND pe.ts >= NOW() - INTERVAL '30 days'
                """,
                (user_id,),
            )
            stats = dict(cur.fetchone() or {})

            cur.execute(
                """
                SELECT hour_of_day, COUNT(*) as cnt
                FROM playback_events
                WHERE user_id = %s AND event_type IN ('play','complete')
                GROUP BY hour_of_day ORDER BY cnt DESC LIMIT 5
                """,
                (user_id,),
            )
            preferred_hours = [r["hour_of_day"] for r in cur.fetchall()]

            cur.execute(
                """
                INSERT INTO taste_profile
                    (user_id, avg_bpm, avg_energy, avg_valence,
                     completion_rate_30d, skip_rate_30d, preferred_hours)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (user_id) DO UPDATE SET
                    avg_bpm=EXCLUDED.avg_bpm,
                    avg_energy=EXCLUDED.avg_energy,
                    avg_valence=EXCLUDED.avg_valence,
                    completion_rate_30d=EXCLUDED.completion_rate_30d,
                    skip_rate_30d=EXCLUDED.skip_rate_30d,
                    preferred_hours=EXCLUDED.preferred_hours,
                    computed_at=NOW()
                """,
                (
                    user_id,
                    stats.get("avg_bpm"),
                    stats.get("avg_energy"),
                    stats.get("avg_valence"),
                    stats.get("completion_rate"),
                    stats.get("skip_rate"),
                    json.dumps(preferred_hours),
                ),
            )
    return stats
