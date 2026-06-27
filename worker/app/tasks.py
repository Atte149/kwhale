"""Celery task definitions."""
import os
import uuid
from pathlib import Path

import psycopg2
from celery import Celery
from celery.schedules import crontab

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
    "generate-recommendations-daily": {
        "task": "app.tasks.generate_recommendations",
        "schedule": crontab(hour=4, minute=0),
        "args": ("vladik", "hybrid"),
    },
    # Auto-index: pick up tracks added to the library (download -> tag -> scan)
    # and index them into track_features. index_all_tracks is idempotent -- it
    # only queues tracks not already indexed (ok / failed-exhausted), so a
    # steady-state run queues nothing. Closes the incoming -> track_features gap.
    "index-new-tracks": {
        "task": "app.tasks.index_all_tracks",
        "schedule": 900.0,
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
                    results.append(_enrich_with_stream_url(meta, provider))
        except Exception as e:
            print(f"Provider {provider.name} search error: {e}")
    return results[:limit]


@celery_app.task(name="app.tasks.search_provider")
def search_provider(provider_name: str, query: str, limit: int = 20) -> list[dict]:
    provider = get_provider(provider_name)
    if not provider:
        return []
    return [_enrich_with_stream_url(m, provider) for m in provider.search(query, limit=limit)]


def _enrich_with_stream_url(meta, provider):
    d = meta.to_dict()
    try:
        d["stream_url"] = provider.resolve(meta.provider_id) or ""
    except Exception:
        d["stream_url"] = ""
    return d


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

    if not provider.is_available():
        _update_queue(
            task_id, "failed",
            error=f"Provider '{provider.name}' is not configured (missing key/token)",
        )
        return

    dest_dir = INCOMING_DIR / task_id
    _update_queue(task_id, "running", pct=10)

    try:
        filepath = provider.download(provider_id, dest_dir)
    except Exception as e:
        _update_queue(task_id, "failed", error=f"{provider.name} download error: {e}"[:500])
        return
    if not filepath:
        _update_queue(
            task_id, "failed",
            error=f"{provider.name} returned no file for '{provider_id}' "
                  "(track unavailable in region, auth, or network error)",
        )
        return

    # Download the high-res cover alongside the audio file. The tagger moves
    # cover.jpg from incoming/<task_id>/ to the album folder in the library
    # so Navidrome picks it up on its next scan. Failure here is non-fatal —
    # the track still ships; it just won't have a cover until someone adds one.
    _update_queue(task_id, "running", pct=70)
    try:
        meta = _find_meta_for(provider, provider_id, query)
        if meta and meta.cover_url:
            cover_path = provider.fetch_cover(meta.cover_url, dest_dir)
            if cover_path:
                print(f"Cover saved: {cover_path}")
    except Exception as e:
        print(f"Cover fetch error for {provider_id}: {e}")

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
    # The file has been placed in incoming/; the tagger-watcher will pick it
    # up and move it to the library automatically.  Signal completion here
    # so the UI stops showing a spinner.
    _update_queue(task_id, "done", pct=100)


def _find_meta_for(provider, provider_id: str, query: str | None):
    """Best-effort lookup of TrackMeta for a downloaded track, so the worker
    can pull the cover_url and save the cover alongside the audio. Searches
    by query when given, otherwise by provider_id. Returns None on any miss
    — cover download is opportunistic.
    """
    try:
        # First try a search by the original query (catches user-typed titles).
        if query:
            for m in provider.search(query, limit=5):
                if m.provider_id == provider_id:
                    return m
        # Fallback: search by provider_id (works for numeric ids).
        for m in provider.search(provider_id, limit=5):
            if m.provider_id == provider_id:
                return m
    except Exception:
        return None
    return None


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
    """Index all tracks not yet in track_features.

    First pass: drain the `artists_indexed_at IS NULL` backfill cheaply
    (FLAC read + UPDATE, no Essentia, no LLM). New tracks need their
    collaborative cast populated even if the heavy audio analysis fails.

    Second pass: queue any track that hasn't been fully indexed (ok or
    failed-retry-budget-exhausted) for the full index_track task.
    """
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

            # Cheap backfill: tracks already exist but lack all_artists.
            cur.execute(
                "SELECT navidrome_id FROM track_features "
                "WHERE artists_indexed_at IS NULL"
            )
            needs_artists = [r[0] for r in cur.fetchall()]

    for track_id in needs_artists:
        backfill_artists.delay(track_id)

    queued = 0
    for track_id, filepath, title, artist in tracks:
        if track_id not in indexed:
            index_track.delay(track_id)
            queued += 1

    print(
        f"Queued {queued} tracks for indexing, "
        f"{len(needs_artists)} for artists backfill"
    )
    return queued


@celery_app.task(name="app.tasks.backfill_artists")
def backfill_artists(navidrome_id: str) -> bool:
    """Populate all_artists + artists_indexed_at for one track.

    Lightweight: opens the FLAC, parses the `artists` tag, and UPDATEs
    the row. No Essentia, no LLM, no embedding service. Used to fill
    `all_artists` for tracks that were indexed before the column
    existed, and as a cheap first pass for new tracks so the
    collaborative search works even if audio analysis later fails.
    """
    import sqlite3
    from .indexer import _read_all_artists

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
            print(f"backfill_artists sqlite error: {e}")

    if not filepath:
        return False

    abs_path = _resolve_filepath(filepath)
    if not os.path.exists(abs_path):
        return False

    all_artists = _read_all_artists(abs_path, title or "")
    all_artists_text = " ".join(all_artists)

    try:
        with _db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE track_features
                       SET all_artists=%s,
                           all_artists_text=%s,
                           artists_indexed_at=NOW(),
                           updated_at=NOW()
                     WHERE navidrome_id=%s
                    """,
                    (all_artists, all_artists_text, navidrome_id),
                )
        return True
    except Exception as e:
        print(f"backfill_artists db error for {navidrome_id}: {e}")
        return False


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


@celery_app.task(name="app.tasks.retag_library")
def retag_library(force: bool = False, limit: int | None = None) -> dict:
    """Scan the library, classify tracks by tag quality, and retag bad ones.

    Classification: 'bad' (empty/generic tags), 'uncertain' (filename mismatch),
    'good' (valid tags). Only bad + uncertain are retagged unless force=True.

    Backups old tags to tag_revisions. Triggers Navidrome scan after.
    """
    from .retagger import retag_library as _retag

    stats = _retag(force=force, limit=limit)

    # Trigger Navidrome rescan so new tags propagate
    try:
        import hashlib, secrets, httpx
        salt = secrets.token_hex(6)
        token = hashlib.md5(
            f"{os.environ.get('NAVIDROME_PASSWORD', '')}{salt}".encode()
        ).hexdigest()
        nav_url = os.environ.get("NAVIDROME_URL", "http://navidrome:4533")
        nav_user = os.environ.get("NAVIDROME_USERNAME", "admin")
        with httpx.Client(timeout=10.0) as client:
            client.get(
                f"{nav_url}/rest/startScan.view",
                params={"u": nav_user, "t": token, "s": salt,
                        "v": "1.16.1", "c": "kwhale-retag", "f": "json"},
            )
    except Exception as e:
        print(f"Navidrome scan trigger failed: {e}")

    print(f"Retag complete: {stats}")
    return stats


@celery_app.task(name="app.tasks.scan_library_tags")
def scan_library_tags() -> dict:
    """Scan library and classify tracks without retagging. For status endpoint."""
    from .retagger import scan_library as _scan

    results = _scan()
    stats = {
        "total": len(results),
        "good": sum(1 for r in results if r["classification"] == "good"),
        "bad": sum(1 for r in results if r["classification"] == "bad"),
        "uncertain": sum(1 for r in results if r["classification"] == "uncertain"),
        "bad_tracks": [r for r in results if r["classification"] == "bad"][:100],
    }
    return stats
