"""KWhale MCP Server — all tools for AI agents.

Transport: streamable-http on port 8090 (/mcp endpoint).
Connect with: Claude Desktop, Cursor, MCP Inspector, OpenAI Agents SDK.

Tools:
  Library:       search_library, get_song, get_similar_tracks
  Discovery:     search_sources, acquire_track, get_download_status
  Telemetry:     get_taste_profile, get_listening_stats
  Playlists:     create_mood_playlist, get_recommendations
  Admin:         trigger_indexing, list_providers
"""
import json
import os
from datetime import datetime, timedelta

import asyncpg
import httpx
from fastmcp import FastMCP

DATABASE_URL = os.environ.get("DATABASE_URL", "")
NAVIDROME_URL = os.environ.get("NAVIDROME_URL", "http://navidrome:4533")
NAVIDROME_USERNAME = os.environ.get("NAVIDROME_USERNAME", "admin")
NAVIDROME_PASSWORD = os.environ.get("NAVIDROME_PASSWORD", "admin")
API_URL = os.environ.get("KWHALE_API_URL", "http://api:8000")

mcp = FastMCP("kwhale")

_pool: asyncpg.Pool | None = None


async def _db() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    return _pool


# ── Library tools ─────────────────────────────────────────────────────────────

@mcp.tool()
async def search_library(query: str, limit: int = 20) -> list[dict]:
    """Search the local music library by title, artist, or album.

    Args:
        query: Search text (e.g. "Radiohead", "Creep", "Radiohead - Creep")
        limit: Maximum results to return (default 20, max 100)

    Returns:
        List of track objects with id, title, artist, album, duration, vibe tags
    """
    import hashlib, secrets
    salt = secrets.token_hex(6)
    token = hashlib.md5(f"{NAVIDROME_PASSWORD}{salt}".encode()).hexdigest()
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(
            f"{NAVIDROME_URL}/rest/search3.view",
            params={"u": NAVIDROME_USERNAME, "t": token, "s": salt,
                    "v": "1.16.1", "c": "kwhale-mcp", "f": "json",
                    "query": query, "songCount": limit, "artistCount": 0, "albumCount": 0},
        )
    resp = r.json().get("subsonic-response", {})
    songs = resp.get("searchResult3", {}).get("song", [])

    pool = await _db()
    ids = [s["id"] for s in songs]
    if ids:
        rows = await pool.fetch(
            "SELECT navidrome_id, bpm, energy, valence, danceability, vibe_tags "
            "FROM track_features WHERE navidrome_id = ANY($1)", ids
        )
        feat = {r["navidrome_id"]: r for r in rows}
    else:
        feat = {}

    result = []
    for s in songs:
        f = feat.get(s["id"])
        entry = {
            "id": s["id"], "title": s["title"],
            "artist": s.get("artist"), "album": s.get("album"),
            "duration_sec": s.get("duration"),
        }
        if f:
            entry["vibe"] = {
                "bpm": f["bpm"], "energy": f["energy"],
                "valence": f["valence"], "tags": json.loads(f["vibe_tags"] or "[]"),
            }
        result.append(entry)
    return result


@mcp.tool()
async def get_similar_tracks(track_id: str, limit: int = 10) -> list[dict]:
    """Find tracks similar to the given track using audio feature similarity.

    Args:
        track_id: Navidrome track ID
        limit: Number of similar tracks to return (default 10)

    Returns:
        List of similar tracks with similarity score (0–1)
    """
    pool = await _db()
    base = await pool.fetchrow(
        "SELECT features_vector FROM track_features WHERE navidrome_id=$1", track_id
    )
    if not base or base["features_vector"] is None:
        return []

    rows = await pool.fetch(
        """
        SELECT navidrome_id, title, artist,
               1 - (features_vector <=> $1::vector) AS score
        FROM track_features
        WHERE navidrome_id != $2 AND features_vector IS NOT NULL
        ORDER BY features_vector <=> $1::vector
        LIMIT $3
        """,
        base["features_vector"], track_id, limit,
    )
    return [{"id": r["navidrome_id"], "title": r["title"],
             "artist": r["artist"], "score": float(r["score"])} for r in rows]


# ── Discovery + acquisition tools ─────────────────────────────────────────────

@mcp.tool()
async def search_sources(query: str, limit: int = 10) -> list[dict]:
    """Search for tracks across all enabled streaming source plugins (ICM, Yandex, etc.).
    Use this to find tracks NOT yet in the local library.

    Args:
        query: Artist and/or track name (e.g. "The Cure - Lovesong")
        limit: Maximum results (default 10)

    Returns:
        List of results with provider, provider_id, title, artist, album
    """
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(
            f"{API_URL}/internal/search-providers",
            json={"query": query, "limit": limit},
        )
    return r.json() if r.status_code == 200 else []


@mcp.tool()
async def acquire_track(query: str = None, provider: str = None, provider_id: str = None) -> dict:
    """Download a track from a streaming source into the local library.
    The track will be auto-tagged and available in the library after ~30–120 seconds.

    Args:
        query: Free-text search (e.g. "Radiohead - Creep"). Used if provider/provider_id not given.
        provider: Source plugin name ("icm", "yandex", "deezer")
        provider_id: Provider-specific track ID

    Returns:
        task_id and status ("queued")
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(
            f"{API_URL}/internal/acquire",
            json={"query": query, "provider": provider, "provider_id": provider_id},
        )
    return r.json()


@mcp.tool()
async def get_download_status(task_id: str) -> dict:
    """Check the status of a download task.

    Args:
        task_id: Task ID returned by acquire_track

    Returns:
        status, progress_pct, navidrome_id (when done), error (if failed)
    """
    pool = await _db()
    row = await pool.fetchrow(
        "SELECT status, progress_pct, navidrome_id, error, created_at "
        "FROM download_queue WHERE id=$1", task_id
    )
    if not row:
        return {"error": "Task not found"}
    return dict(row)


# ── Telemetry + taste profile ──────────────────────────────────────────────────

@mcp.tool()
async def get_taste_profile(user_id: str = "default") -> dict:
    """Get the user's taste profile derived from their listening history.
    Includes preferred BPM, energy, valence, peak listening hours, completion and skip rates.

    Args:
        user_id: User identifier (default "default" for single-user setups)

    Returns:
        Taste profile with audio preferences and listening behaviour metrics
    """
    pool = await _db()
    row = await pool.fetchrow("SELECT * FROM taste_profile WHERE user_id=$1", user_id)
    if not row:
        return {"message": "No taste profile yet. Need more listening history."}
    return dict(row)


@mcp.tool()
async def get_listening_stats(user_id: str = "default", days: int = 30) -> dict:
    """Get listening statistics for the past N days.

    Args:
        user_id: User identifier
        days: Number of days to look back (default 30)

    Returns:
        total_plays, top_artists, top_tracks, avg_completion, skip_rate,
        busiest_hour, busiest_day_of_week
    """
    pool = await _db()
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()

    total = await pool.fetchval(
        "SELECT COUNT(*) FROM playback_events WHERE user_id=$1 AND ts > $2 AND event_type='play'",
        user_id, since,
    )
    top_tracks = await pool.fetch(
        """
        SELECT navidrome_id, tf.title, tf.artist,
               COUNT(*) FILTER (WHERE event_type='play') as plays
        FROM playback_events pe
        LEFT JOIN track_features tf ON tf.navidrome_id = pe.navidrome_id
        WHERE pe.user_id=$1 AND pe.ts > $2
        GROUP BY pe.navidrome_id, tf.title, tf.artist
        ORDER BY plays DESC LIMIT 10
        """,
        user_id, since,
    )
    busiest_hour = await pool.fetchval(
        """
        SELECT hour_of_day FROM playback_events
        WHERE user_id=$1 AND ts > $2 AND event_type='play'
        GROUP BY hour_of_day ORDER BY COUNT(*) DESC LIMIT 1
        """,
        user_id, since,
    )
    avg_completion = await pool.fetchval(
        "SELECT AVG(completion_pct) FROM playback_events WHERE user_id=$1 AND ts > $2",
        user_id, since,
    )
    skip_rate = await pool.fetchval(
        """
        SELECT COUNT(*) FILTER (WHERE skipped)::float / NULLIF(COUNT(*), 0)
        FROM playback_events WHERE user_id=$1 AND ts > $2
        """,
        user_id, since,
    )

    return {
        "period_days": days,
        "total_plays": total,
        "avg_completion_pct": float(avg_completion or 0),
        "skip_rate": float(skip_rate or 0),
        "busiest_hour": busiest_hour,
        "top_tracks": [dict(r) for r in top_tracks],
    }


# ── Recommendations ───────────────────────────────────────────────────────────

@mcp.tool()
async def get_recommendations(
    user_id: str = "default",
    algorithm: str = "hybrid",
    limit: int = 20,
) -> list[dict]:
    """Get personalised track recommendations.

    Args:
        user_id: User identifier
        algorithm: "hybrid" (ALS + content), "als" (collaborative), "content" (audio similarity)
        limit: Number of recommendations (default 20, max 50)

    Returns:
        List of recommended tracks with navidrome_id, title, artist
    """
    pool = await _db()
    row = await pool.fetchrow(
        "SELECT track_ids FROM recommendations "
        "WHERE user_id=$1 AND algorithm=$2 ORDER BY date DESC LIMIT 1",
        user_id, algorithm,
    )
    if not row:
        return []

    track_ids = row["track_ids"][:limit]
    if not track_ids:
        return []

    rows = await pool.fetch(
        "SELECT navidrome_id, title, artist, album, vibe_tags "
        "FROM track_features WHERE navidrome_id = ANY($1)",
        track_ids,
    )
    feat = {r["navidrome_id"]: r for r in rows}

    result = []
    for tid in track_ids:
        f = feat.get(tid, {})
        result.append({
            "id": tid,
            "title": f.get("title"),
            "artist": f.get("artist"),
            "album": f.get("album"),
            "vibe_tags": json.loads(f.get("vibe_tags") or "[]"),
        })
    return result


@mcp.tool()
async def create_mood_playlist(
    description: str,
    limit: int = 20,
    user_id: str = "default",
) -> list[dict]:
    """Create a playlist based on a mood or vibe description using AI.

    Args:
        description: Natural language description, e.g. "late night melancholic indie",
                     "energetic morning workout", "focus music without lyrics"
        limit: Number of tracks in the playlist (default 20)
        user_id: User identifier

    Returns:
        List of matching tracks ordered by relevance
    """
    pool = await _db()

    # Use vibe_tags full-text search + taste profile weighting
    taste = await pool.fetchrow("SELECT avg_energy, avg_valence FROM taste_profile WHERE user_id=$1", user_id)

    keywords = description.lower().split()
    energy_hint = None
    if any(w in keywords for w in ["energetic", "upbeat", "workout", "pump"]):
        energy_hint = (0.6, 1.0)
    elif any(w in keywords for w in ["calm", "chill", "relax", "sleep", "ambient"]):
        energy_hint = (0.0, 0.4)

    query = "SELECT navidrome_id, title, artist, vibe_tags, energy FROM track_features WHERE features_vector IS NOT NULL"
    params = []

    if energy_hint:
        query += f" AND energy BETWEEN ${len(params)+1} AND ${len(params)+2}"
        params.extend(energy_hint)

    query += f" ORDER BY RANDOM() LIMIT ${len(params)+1}"
    params.append(limit * 3)

    rows = await pool.fetch(query, *params)

    # Filter by keyword match against vibe_tags
    matched = []
    for r in rows:
        tags = json.loads(r["vibe_tags"] or "[]")
        score = sum(1 for kw in keywords if any(kw in tag.lower() for tag in tags))
        matched.append((score, r))

    matched.sort(key=lambda x: -x[0])
    return [
        {"id": r["navidrome_id"], "title": r["title"], "artist": r["artist"],
         "vibe_tags": json.loads(r["vibe_tags"] or "[]")}
        for _, r in matched[:limit]
    ]


# ── Admin tools ───────────────────────────────────────────────────────────────

@mcp.tool()
async def trigger_indexing(track_id: str = None) -> dict:
    """Trigger Essentia audio feature extraction.

    Args:
        track_id: Specific track ID to index. If omitted, indexes ALL unindexed tracks.

    Returns:
        task_id and number of tracks queued
    """
    async with httpx.AsyncClient(timeout=5.0) as client:
        if track_id:
            r = await client.post(f"{API_URL}/api/vibe/{track_id}/index")
        else:
            r = await client.post(f"{API_URL}/api/vibe/index-all")
    return r.json()


@mcp.tool()
async def list_providers() -> list[dict]:
    """List all enabled source provider plugins (streaming services).

    Returns:
        List of providers with name and availability status
    """
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.get(f"{API_URL}/api/discover")
    return r.json() if r.status_code == 200 else []


