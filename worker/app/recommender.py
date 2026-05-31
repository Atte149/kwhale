"""Recommendation engine: ALS collaborative + content-based (pgvector) hybrid."""
import os
import json
from datetime import date

import numpy as np
import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ.get("DATABASE_URL", "")


def _get_conn():
    return psycopg2.connect(DATABASE_URL)


def _als_recommendations(user_id: str, n: int = 30) -> list[str]:
    """ALS collaborative filtering over playback_events."""
    try:
        import implicit
        from scipy.sparse import csr_matrix

        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT navidrome_id,
                           COUNT(*) FILTER (WHERE event_type IN ('play','complete')) AS plays,
                           AVG(completion_pct) AS avg_completion
                    FROM playback_events
                    WHERE user_id = %s AND navidrome_id IS NOT NULL
                    GROUP BY navidrome_id
                    """,
                    (user_id,),
                )
                rows = cur.fetchall()

        if len(rows) < 5:
            return []

        tracks = [r[0] for r in rows]
        track_idx = {t: i for i, t in enumerate(tracks)}
        confidences = [
            float(r[1]) * (1 + float(r[2] or 0))
            for r in rows
        ]

        data = np.array(confidences, dtype=np.float32)
        col = np.array([track_idx[r[0]] for r in rows], dtype=np.int32)
        row = np.zeros(len(rows), dtype=np.int32)
        user_items = csr_matrix((data, (row, col)), shape=(1, len(tracks)))

        factors = min(16, len(tracks) - 1)
        model = implicit.als.AlternatingLeastSquares(
            factors=factors, iterations=15, regularization=0.1
        )
        model.fit(user_items.T)
        ids, _ = model.recommend(0, user_items, N=n, filter_already_liked=True)
        return [tracks[i] for i in ids]
    except Exception as e:
        print(f"ALS error: {e}")
        return []


def _content_recommendations(user_id: str, n: int = 20) -> list[str]:
    """Content-based: pgvector cosine from avg of loved tracks."""
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                # loved = tracks with good completion and multiple plays
                cur.execute(
                    """
                    SELECT navidrome_id
                    FROM playback_events
                    WHERE user_id = %s
                    GROUP BY navidrome_id
                    HAVING AVG(completion_pct) > 0.6
                       AND COUNT(*) FILTER (WHERE event_type IN ('play','complete')) >= 2
                    """,
                    (user_id,),
                )
                loved_ids = [r[0] for r in cur.fetchall()]

                if not loved_ids:
                    return []

                cur.execute(
                    """
                    SELECT AVG(features_vector) as avg_vec
                    FROM track_features
                    WHERE navidrome_id = ANY(%s)
                      AND features_vector IS NOT NULL
                    """,
                    (loved_ids,),
                )
                avg_row = cur.fetchone()
                if not avg_row or avg_row[0] is None:
                    return []

                avg_vec = avg_row[0]
                cur.execute(
                    """
                    SELECT navidrome_id
                    FROM track_features
                    WHERE navidrome_id != ALL(%s)
                      AND features_vector IS NOT NULL
                    ORDER BY features_vector <=> %s::vector
                    LIMIT %s
                    """,
                    (loved_ids, avg_vec, n),
                )
                return [r[0] for r in cur.fetchall()]
    except Exception as e:
        print(f"Content recs error: {e}")
        return []


def generate_recommendations(user_id: str, algorithm: str = "hybrid") -> list[str]:
    als_ids = [] if algorithm == "content" else _als_recommendations(user_id, n=40)
    content_ids = [] if algorithm == "als" else _content_recommendations(user_id, n=40)

    seen = set()
    merged = []
    for tid in als_ids + content_ids:
        if tid not in seen:
            seen.add(tid)
            merged.append(tid)

    # Don't persist an empty set — let the API serve a cold-start fallback instead.
    if not merged:
        return []

    # Personal re-ranking: favourites, frequency, completion, time-of-day,
    # recency, skip-penalty (see personal_score.compute_personal_scores).
    scores = {}
    try:
        from .personal_score import compute_personal_scores
        scores = compute_personal_scores(user_id, merged)
        prior = {tid: i for i, tid in enumerate(merged)}
        # higher personal score first; ties keep original ALS/content order
        merged.sort(key=lambda t: (-(scores.get(t, 0.0)), prior[t]))
    except Exception as e:
        print(f"personal re-rank failed: {e}")

    final = merged[:20]
    final_scores = {t: scores.get(t, 0.0) for t in final}

    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO recommendations (user_id, date, algorithm, track_ids, scores)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (user_id, date, algorithm) DO UPDATE
                SET track_ids=EXCLUDED.track_ids, scores=EXCLUDED.scores, generated_at=NOW()
                """,
                (user_id, date.today().isoformat(), algorithm,
                 json.dumps(final), json.dumps(final_scores)),
            )
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
