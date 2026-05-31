"""Personal scoring layer.

Computes a per-track personalisation coefficient from listening behaviour.
Used by recommender.generate_recommendations() to re-rank candidate tracks,
and exposed via /api/recs so the formula is transparent.

Final coefficient (per track t, for the current hour H):

    score(t) = W_fav   * fav(t)
             + W_freq  * freq(t)
             + W_compl * completion(t)
             + W_time  * time_affinity(t, H)
             + W_recent* recency(t)
             - W_skip  * skip_penalty(t)

All component terms are normalised to roughly 0..1 so the weights below
directly express their relative influence. See compute_personal_scores().
"""
import os
import math
from datetime import datetime, timezone, timedelta

import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ.get("DATABASE_URL", "")
LOCAL_TZ_OFFSET = int(os.environ.get("LOCAL_TZ_OFFSET_HOURS", "3"))  # Europe/Moscow

# ── Weights (sum of positive weights ~= 1.0) ────────────────────────────────
W_FAV     = 0.30   # track is starred / favourited
W_FREQ    = 0.20   # how often the user plays it
W_COMPL   = 0.20   # average completion ratio (anti-skip signal)
W_TIME    = 0.20   # affinity between the track and the current time-of-day
W_RECENT  = 0.10   # recently played (gentle recency boost, decays over 30d)
W_SKIP    = 0.25   # penalty for frequent skips

# Time-of-day buckets (local hours) for "morning jazz" style affinity.
def _bucket(hour: int) -> str:
    if 5 <= hour < 11:  return "morning"
    if 11 <= hour < 17: return "day"
    if 17 <= hour < 23: return "evening"
    return "night"


def _now_local_hour() -> int:
    return (datetime.now(timezone.utc) + timedelta(hours=LOCAL_TZ_OFFSET)).hour


def _conn():
    return psycopg2.connect(DATABASE_URL)


def compute_personal_scores(user_id: str, track_ids: list[str],
                            current_hour: int | None = None) -> dict[str, float]:
    """Return {navidrome_id: coefficient} for the given candidate track_ids.

    Tracks with no history get a neutral 0.0 (they keep their ALS/content rank).
    """
    if not track_ids:
        return {}
    if current_hour is None:
        current_hour = _now_local_hour()
    cur_bucket = _bucket(current_hour)

    scores: dict[str, float] = {tid: 0.0 for tid in track_ids}

    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            # Per-track behavioural aggregates over the last 90 days.
            cur.execute(
                """
                SELECT navidrome_id,
                       COUNT(*) FILTER (WHERE event_type IN ('play','complete'))      AS plays,
                       COUNT(*) FILTER (WHERE event_type = 'skip')                     AS skips,
                       AVG(completion_pct)                                             AS avg_compl,
                       MAX(ts)                                                         AS last_ts,
                       COUNT(*) FILTER (
                           WHERE event_type IN ('play','complete')
                             AND _bucket_match(hour_of_day, %s)
                       )                                                               AS bucket_plays
                FROM playback_events
                WHERE user_id = %s
                  AND navidrome_id = ANY(%s)
                  AND ts >= NOW() - INTERVAL '90 days'
                GROUP BY navidrome_id
                """.replace(
                    "_bucket_match(hour_of_day, %s)",
                    _bucket_sql(cur_bucket),
                ),
                (user_id, track_ids),
            )
            rows = {r["navidrome_id"]: r for r in cur.fetchall()}

            # Global max plays for frequency normalisation.
            max_plays = max((r["plays"] or 0) for r in rows.values()) if rows else 0

    # Starred set from Navidrome (favourites).
    starred = _starred_ids(track_ids)

    now = datetime.now(timezone.utc)
    for tid in track_ids:
        r = rows.get(tid)
        comp = 0.0

        fav = 1.0 if tid in starred else 0.0
        comp += W_FAV * fav

        if r:
            plays = r["plays"] or 0
            skips = r["skips"] or 0
            avg_compl = float(r["avg_compl"] or 0.0)
            bucket_plays = r["bucket_plays"] or 0

            # frequency: log-scaled and normalised to the user's most-played track
            if max_plays > 0:
                freq = math.log1p(plays) / math.log1p(max_plays)
            else:
                freq = 0.0
            comp += W_FREQ * freq

            # completion ratio (already 0..1)
            comp += W_COMPL * min(max(avg_compl, 0.0), 1.0)

            # time-of-day affinity: share of this track's plays that fall in the
            # current bucket → "I usually play jazz in the morning" boosts jazz now
            if plays > 0:
                time_aff = bucket_plays / plays
                comp += W_TIME * time_aff

            # recency: 1.0 if played today, decays with 30-day half-life
            if r["last_ts"]:
                days = max((now - r["last_ts"]).total_seconds() / 86400.0, 0.0)
                recency = 0.5 ** (days / 30.0)
                comp += W_RECENT * recency

            # skip penalty: fraction of skips among all interactions
            total = plays + skips
            if total > 0:
                comp -= W_SKIP * (skips / total)

        scores[tid] = round(comp, 4)

    return scores


def _bucket_sql(bucket: str) -> str:
    ranges = {
        "morning": "(hour_of_day >= 5 AND hour_of_day < 11)",
        "day":     "(hour_of_day >= 11 AND hour_of_day < 17)",
        "evening": "(hour_of_day >= 17 AND hour_of_day < 23)",
        "night":   "(hour_of_day >= 23 OR hour_of_day < 5)",
    }
    return ranges[bucket]


def _starred_ids(track_ids: list[str]) -> set[str]:
    """Fetch starred song ids from Navidrome via Subsonic, intersect with candidates."""
    import hashlib, secrets, urllib.parse, urllib.request, json
    base = os.environ.get("NAVIDROME_URL", "http://navidrome:4533")
    user = os.environ.get("NAVIDROME_USERNAME", "admin")
    pwd = os.environ.get("NAVIDROME_PASSWORD", "admin")
    salt = secrets.token_hex(6)
    token = hashlib.md5(f"{pwd}{salt}".encode()).hexdigest()
    params = urllib.parse.urlencode({
        "u": user, "t": token, "s": salt, "v": "1.16.1", "c": "kwhale", "f": "json",
    })
    try:
        req = urllib.request.Request(f"{base}/rest/getStarred2.view?{params}",
                                     headers={"User-Agent": "kwhale/1.0"})
        data = json.load(urllib.request.urlopen(req, timeout=10))
        songs = data.get("subsonic-response", {}).get("starred2", {}).get("song", [])
        ids = {s["id"] for s in songs}
        return ids & set(track_ids)
    except Exception as e:
        print(f"[personal] starred fetch failed: {e}")
        return set()
