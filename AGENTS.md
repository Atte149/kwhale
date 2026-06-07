# AGENTS.md — KWhale

Quick reference for AI agents working in this repo.

## Commands

```bash
# Start the stack
docker compose up -d

# Rebuild a single service after code change
docker compose up -d --build api
docker compose up -d --build worker
docker compose up -d --build mcp

# View logs
docker compose logs -f api
docker compose logs -f worker

# Check API health
curl http://localhost:19000/healthz

# Get auth token
curl -X POST http://localhost:19000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"melorise"}'

# Trigger indexing
curl -X POST http://localhost:19000/api/vibe/index-all \
  -H "Authorization: Bearer $TOKEN"

# Check MCP with inspector
npx @modelcontextprotocol/inspector http://localhost:8090/mcp
```

## Architecture

```
Navic (Flutter client)
  ↓ Bearer JWT, JSON
FastAPI kwhale-api :19000
  ↓ internal HTTP         ↓ SQL
Navidrome :4535          Postgres/pgvector :5432
(media engine)           track_features, playback_events, recommendations...
  ↑ reads                      ↑
library/                 Worker (Celery)
  ↑ writes                     source plugins (icm, yandex, deezer)
incoming/ → tagger → library/  Essentia indexer, recommender
                               MCP server :8090 (fastmcp, streamable-http)
```

## Adding a new source plugin

1. Create `worker/app/providers/my_source.py`
2. Subclass `BaseProvider` from `.base`
3. Implement `search()`, `resolve()`, `download()`
4. Set `name = "my_source"` on the class
5. Rebuild worker: `docker compose up -d --build worker`

No registration needed — the registry auto-discovers it.

## Key files

| File | Purpose |
|------|---------|
| `docker-compose.yml` | All services |
| `api/app/main.py` | FastAPI app + router registration |
| `api/app/routers/events.py` | Telemetry ingest |
| `api/app/routers/discover.py` | Remote source search + download queue |
| `api/app/routers/recommendations.py` | Rec feed + taste profile |
| `worker/app/tasks.py` | All Celery tasks |
| `worker/app/providers/` | Source plugin directory |
| `worker/app/indexer.py` | Essentia + embeddings + vibe tags |
| `worker/app/recommender.py` | ALS + content recs |
| `mcp/app/server.py` | MCP tools (fastmcp) |
| `tagger/app/main.py` | Auto-tagger service |
| `postgres/init.sql` | DB schema |
| `docs/API.md` | Full API reference |

## Environment

| File | Used by |
|------|---------|
| `.env` | docker-compose substitution (DATA_ROOT, passwords) |
| `api/.env` | API container (JWT, OpenAI, ICM keys) |
| `mcp/.env` | MCP container (DATABASE_URL) |
| `worker/.env` | Worker container (Yandex token, ICM key, DB) |

## Data paths (all under DATA_ROOT=/files/kwhale/data)

| Path | What |
|------|------|
| `music/library/` | Organized library (Navidrome reads this) |
| `music/incoming/` | Download zone (tagger watches this) |
| `music/failed/` | Files tagger couldn't identify |
| `navidrome/` | Navidrome state |
| `postgres/` | PostgreSQL data |
| `redis/` | Redis data |
