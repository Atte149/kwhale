# AGENTS.md — KWhale

Quick reference for AI agents (incl. small models) working in this repo.

## Canon / golden rules
- **This directory IS the canon**: `/files/kwhale/server` = git repo = what actually
  runs in Docker. There is no separate dev tree. Edit here, rebuild, commit.
- Push to **forgejo** only (`git push` → forgejo/main; public github is not used).
- **One track ID everywhere**: `navidrome_id` (== Subsonic song id). No other ID space.
- **One URL format**: the API returns absolute public Subsonic URLs for stream/cover
  (`navidrome.stream_url` / `cover_url`); they load without an auth header.
- Keep it simple — one responsibility per service, one recs path, one provider interface.

## Commands (docker needs sudo on this host)
```bash
sudo docker compose up -d                          # start stack
sudo docker compose build worker && \
  sudo docker compose up -d --force-recreate worker worker-beat   # after worker code change
sudo docker compose build api  && sudo docker compose up -d api   # after api code change
sudo docker compose logs -f worker
curl http://localhost:19000/healthz                # api health
# auth token (real account: vladik / melorise)
curl -sX POST https://music.dueattendant149.org/api/auth/login \
  -H 'Content-Type: application/json' -d '{"username":"vladik","password":"melorise"}'
bash scripts/smoke.sh                              # end-to-end smoke test
```
Note: `worker-beat` and `tagger-watcher` reuse the `worker`/`tagger` images — after a
rebuild, **force-recreate them** or they keep the old code.

## Architecture
```
Flutter client (fork of dddevid/Musly)
  ↓ Subsonic /rest/* (browse/stream/cover/scrobble) ── Caddy ──> Navidrome
  ↓ /api/* (Bearer JWT, JSON) ───────────────────────────────> kwhale-api :19000
kwhale-api → Postgres/pgvector (track_features, playback_events, taste_profile,
             recommendations, download_queue, provider_track_map)
           → Navidrome (Subsonic) ; → Celery worker (redis)
worker: index_track (Essentia + bge-m3 lyrics emb + LLM vibe tags),
        recommender v2 (pgvector retrieval + layered scoring + LLM curation),
        download_provider_track (source plugins)
beat: index_all_tracks /15min (auto-index), update_all_taste_profiles /day
incoming/ → tagger → library/ → Navidrome scan → auto-index → track_features
MCP :8090 (fastmcp) — tools for the chat surface
LLM: opencode go (OPENAI_API_BASE=https://opencode.ai/zen/go/v1); recs use minimax-m3
```

## Recommendations (recommender.py v2)
Per-user. `generate_recommendations(user_id, algorithm)`:
seed (engaged tracks) → pgvector cosine kNN from the taste centroid → transparent
weighted score (W_CONTENT/W_PERSONAL/W_NOVELTY) → one LLM curation call (minimax-m3,
falls back to score order) → always persist a non-empty set (cold-start fallback).
No ALS/collaborative filtering (single-user). New Navidrome account = own recs/history.

## Adding a source plugin (modular, auto-discovered)
1. `worker/app/providers/my_source.py`, subclass `BaseProvider`
2. implement `search()/resolve()/download()`, set `name`, `priority` (lower = preferred)
3. `is_available()` should return False without creds (then it isn't loaded)
4. rebuild worker. Current: **icm** (priority 10, preferred) → **yandex** (20, fallback).
   VK is a future drop-in (needs a VK token).

## Key files
| File | Purpose |
|------|---------|
| `docker-compose.yml` | All services (project name: `server`) |
| `api/app/main.py` | FastAPI app + router registration |
| `api/app/navidrome.py` | Subsonic client + public stream/cover URL builders |
| `api/app/routers/recommendations.py` | Rec feed (reads `recommendations`) |
| `api/app/routers/library.py` | Library proxy + enrich (URL contract) |
| `api/app/routers/discover.py` | Remote search + acquire (download_queue) |
| `worker/app/tasks.py` | Celery tasks + beat schedule |
| `worker/app/recommender.py` | Recommender v2 (retrieval + scoring + LLM curation) |
| `worker/app/indexer.py` | Essentia + embeddings + vibe tags |
| `worker/app/providers/` | Source plugins (base, icm, yandex) |
| `tagger/app/{main,watcher}.py` | Auto-tagger + incoming watcher |
| `postgres/init.sql` | DB schema |

## Environment (.env files are gitignored — secrets live only on disk)
| File | Used by |
|------|---------|
| `.env` | compose substitution (DATA_ROOT, POSTGRES_PASSWORD, NAVIDROME_*) |
| `api/.env` | API (JWT_SECRET, OPENAI_API_BASE/KEY, ICM_*) |
| `worker/.env` | Worker (OPENAI_*, YANDEX_MUSIC_TOKEN, ICM_*, NAVIDROME_DB) |
| `mcp/.env` | MCP (DATABASE_URL, NAVIDROME_*) |

## Data paths (DATA_ROOT=/files/kwhale/data)
| Path | What |
|------|------|
| `music/library/` | Library (real dir; Navidrome reads it as /music) |
| `music/incoming/` | Download zone (tagger watches; `incoming/failed/` is skipped) |
| `navidrome/` `postgres/` `redis/` | Service state |

## Known minor cleanups (not blocking; see STABILIZATION_PLAN.md)
- `api/app/routers/{discover,internal}.py` share near-identical acquire logic (dedupe).
- `indexer.py` spectrogram analysis (mimo-v2-omni) — optional; works but droppable.
- `/api/stream` & `/api/library/cover` endpoints exist but the URL contract now emits
  direct Subsonic URLs — candidates for removal.
- Legacy v1 data under `/files/musicbrain/data` (~1.5 GB) can be pruned (backup exists);
  **do NOT delete `/files/musicbrain/data/music`** unless the library symlink history is
  fully migrated (the live library was relocated out of it).
