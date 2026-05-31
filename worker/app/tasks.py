"""Celery task definitions."""
import os
import uuid
from pathlib import Path

import psycopg2
from celery import Celery

from .providers.registry import get_providers, get_provider
from .indexer import index_track as _index_track
from .recommender import generate_recommendations as _gen_recs, update_taste_profile

DATABASE_URL = os.environ.get("DATABASE_URL", "")
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
INCOMING_DIR = Path(os.environ.get("MUSIC_INCOMING_DIR", "/data/incoming"))
# How many times a track that fails audio analysis is retried before
# index_all_tracks stops re-queuing it (guards against infinite retry loops).
MAX_INDEX_ATTEMPTS = int(os.environ.get("MAX_INDEX_ATTEMPTS", "3"))

celery_app = Celery("kwhale", broker=REDIS_URL, backend=REDIS_URL)
celery_app.conf.task_serializer = "json"
celery_app.conf.result_serializer = "json"
celery_app.conf.beat_schedule = {
    "update-taste-profiles-daily": {
        "task": "app.tasks.update_all_taste_profiles",
        "schedule": 86400.0,
    },
}


def _db():
    return psycopg2.connect(DATABASE_URL)


def _update_queue(task_id: str, status: str, error: str = None, navidrome_id: str = None, pct: float = None):
    try:
        with _db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE download_queue SET status=%s, error=%s, "
                    "navidrome_id=COALESCE(%s, navidrome_id), "
                    "progress_pct=COALESCE(%s, progress_pct), "
                    "updated_at=NOW() "
                    "WHERE id=%s",
                    (status, error, navidrome_id, pct, task_id),
                )
    except Exception as e:
        print(f"Queue update error: {e}")


@celery_app.task(name="app.tasks.search_providers")
def search_providers(query: str, limit: int = 20) -> list[dict]:
    """Search all enabled providers, merge and deduplicate results."""
    providers = get_providers()
    seen = set()
    results = []
    for provider in providers.values():
        try:
            for meta in provider.search(query, limit=limit // max(1, len(providers))):
                key = f"{meta.title.lower()}::{meta.artist.lower()}"
                if key not in seen:
                    seen.add(key)
                    results.append(meta.to_dict())
        except Exception as e:
            print(f"Provider {provider.name} search error: {e}")
    return results[:limit]


@celery_app.task(name="app.tasks.search_provider")
def search_provider(provider_name: str, query: str, limit: int = 20) -> list[dict]:
    provider = get_provider(provider_name)
    if not provider:
        return []
    return [m.to_dict() for m in provider.search(query, limit=limit)]


@celery_app.task(name="app.tasks.download_provider_track", bind=True)
def download_provider_track(self, provider_name: str, provider_id: str, task_id: str, query: str = None):
    _update_queue(task_id, "running", pct=0)
    provider = get_provider(provider_name)

    if not provider:
        # Try all providers if none specified
        if query:
            for p in get_providers().values():
                results = p.search(query, limit=1)
                if results:
                    provider = p
                    provider_id = results[0].provider_id
                    break

    if not provider:
        _update_queue(task_id, "failed", error="No provider available")
        return

    dest_dir = INCOMING_DIR / task_id
    _update_queue(task_id, "running", pct=10)

    filepath = provider.download(provider_id, dest_dir)
    if not filepath:
        _update_queue(task_id, "failed", error="Download failed")
        return

    _update_queue(task_id, "tagging", pct=80)
    # Tagger will pick up the file from incoming/ automatically
    # We record the provider mapping; navidrome_id filled after scan
    try:
        with _db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO provider_track_map (provider, provider_id, status)
                    VALUES (%s, %s, 'pending')
                    ON CONFLICT DO NOTHING
                    """,
                    (provider_name, provider_id),
                )
    except Exception:
        pass

    _update_queue(task_id, "tagging", pct=90)


NAVIDROME_DB = os.environ.get("NAVIDROME_DB", "/navidrome/navidrome.db")
LIBRARY_DIR = os.environ.get("LIBRARY_DIR", "/data/library")


def _resolve_filepath(rel_or_abs: str) -> str:
    """Navidrome stores paths relative to its library root (/music).
    Map them to the worker's library mount."""
    if os.path.isabs(rel_or_abs) and os.path.exists(rel_or_abs):
        return rel_or_abs
    # strip a leading /music/ if present, then join with our LIBRARY_DIR
    rel = rel_or_abs
    for prefix in ("/music/", "music/", "/"):
        if rel.startswith(prefix):
            rel = rel[len(prefix):]
            break
    return os.path.join(LIBRARY_DIR, rel)


@celery_app.task(name="app.tasks.index_track")
def index_track(navidrome_id: str) -> bool:
    """Extract Essentia features + lyrics embedding + vibe tags for one track."""
    import sqlite3
    filepath = title = artist = None
    if Path(NAVIDROME_DB).exists():
        try:
            conn_nd = sqlite3.connect(f"file:{NAVIDROME_DB}?mode=ro&immutable=1", uri=True)
            row = conn_nd.execute(
                "SELECT path, title, artist FROM media_file WHERE id=?", (navidrome_id,)
            ).fetchone()
            conn_nd.close()
            if row:
                filepath, title, artist = row
        except Exception as e:
            print(f"SQLite error: {e}")

    if not filepath:
        print(f"Track {navidrome_id} not found in navidrome DB")
        return False

    abs_path = _resolve_filepath(filepath)
    if not os.path.exists(abs_path):
        print(f"File not found for {navidrome_id}: {abs_path}")
        return False

    return _index_track(navidrome_id, abs_path, artist or "", title or "")


@celery_app.task(name="app.tasks.index_all_tracks")
def index_all_tracks() -> int:
    """Index all tracks not yet in track_features."""
    import sqlite3
    if not Path(NAVIDROME_DB).exists():
        print("Navidrome DB not found")
        return 0

    conn_nd = sqlite3.connect(f"file:{NAVIDROME_DB}?mode=ro&immutable=1", uri=True)
    cur_nd = conn_nd.cursor()
    cur_nd.execute("SELECT id, path, title, artist FROM media_file")
    tracks = cur_nd.fetchall()
    conn_nd.close()

    with _db() as conn:
        with conn.cursor() as cur:
            # Skip tracks already indexed OK, and failed tracks that have used
            # up their retry budget — otherwise a single corrupt file would be
            # re-queued on every pass (infinite retry loop).
            cur.execute(
                "SELECT navidrome_id FROM track_features "
                "WHERE index_status='ok' "
                "   OR (index_status='failed' AND index_attempts >= %s)",
                (MAX_INDEX_ATTEMPTS,),
            )
            indexed = {r[0] for r in cur.fetchall()}

    queued = 0
    for track_id, filepath, title, artist in tracks:
        if track_id not in indexed:
            index_track.delay(track_id)
            queued += 1

    print(f"Queued {queued} tracks for indexing")
    return queued


@celery_app.task(name="app.tasks.generate_recommendations")
def generate_recommendations(user_id: str, algorithm: str = "hybrid") -> list[str]:
    return _gen_recs(user_id, algorithm)


@celery_app.task(name="app.tasks.update_all_taste_profiles")
def update_all_taste_profiles():
    """Daily task: update taste profile for all users who have events."""
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT user_id FROM playback_events")
            users = [r[0] for r in cur.fetchall()]
    for user in users:
        update_taste_profile(user)
        generate_recommendations(user)
