# KWhale Development Guide

Quick reference for developers working on KWhale.

## Development Setup

### Prerequisites
- Docker and Docker Compose
- Python 3.11+ (for local development)
- Node.js 18+ (for MCP inspector)

### Local Development

```bash
# Clone the repository
git clone https://github.com/yourusername/kwhale.git
cd kwhale

# Set up environment
cp .env.example .env
cp api/.env.example api/.env
cp worker/.env.example worker/.env
cp mcp/.env.example mcp/.env
mkdir -p data/music/{library,incoming,failed}
mkdir -p data/{navidrome,postgres,redis}

# Start services
docker compose up -d

# View logs
docker compose logs -f api
docker compose logs -f worker
```

### Rebuilding After Code Changes

```bash
# Rebuild a single service
docker compose up -d --build api
docker compose up -d --build worker
docker compose up -d --build mcp

# Rebuild all services
docker compose build
docker compose up -d
```

## Architecture Overview

```
Client (Flutter app)
  ↓ Bearer JWT, JSON
FastAPI (kwhale-api) :19000
  ↓ internal HTTP         ↓ SQL
Navidrome :4535          PostgreSQL/pgvector :5432
(media engine)           track_features, playback_events, recommendations
  ↑ reads                      ↑
library/                 Worker (Celery)
  ↑ writes                     - Source plugins (ICM, Yandex, Deezer)
incoming/ → tagger → library/  - Essentia indexer
                               - Recommender
                               - MCP server :8090
```

See [ARCHITECTURE.md](../ARCHITECTURE.md) for detailed architecture documentation.

## Project Structure

```
kwhale/
├── api/                    # FastAPI service
│   ├── app/
│   │   ├── main.py        # FastAPI app + router registration
│   │   ├── routers/       # API endpoints
│   │   ├── auth.py        # JWT authentication
│   │   ├── navidrome.py   # Navidrome client
│   │   └── prompt_recs.py # LLM agent for recommendations
│   └── tests/
├── worker/                 # Celery worker
│   ├── app/
│   │   ├── tasks.py       # Celery tasks
│   │   ├── indexer.py     # Essentia + embeddings + vibe tags
│   │   ├── recommender.py # ALS + content recommendations
│   │   ├── providers/     # Source plugin directory
│   │   └── llm_client.py  # Shared LLM client
│   └── tests/
├── tagger/                 # Auto-tagger service
│   └── app/
│       ├── main.py        # Tagger API
│       ├── organizer.py   # File organization logic
│       └── watcher.py     # File system watcher
├── mcp/                    # MCP server
│   └── app/
│       └── server.py      # FastMCP tools
├── embedding/              # Embedding service
│   └── app/
│       └── server.py      # bge-m3 embedding API
├── postgres/
│   └── init.sql           # Database schema
├── docs/                   # Documentation
└── scripts/                # Utility scripts
```

## Key Files

| File | Purpose |
|------|---------|
| `docker-compose.yml` | All services configuration |
| `api/app/main.py` | FastAPI app entry point |
| `api/app/routers/events.py` | Telemetry ingest |
| `api/app/routers/discover.py` | Remote source search + download |
| `api/app/routers/recommendations.py` | Recommendation feed |
| `worker/app/tasks.py` | All Celery tasks |
| `worker/app/providers/` | Source plugin directory |
| `worker/app/indexer.py` | Audio feature extraction |
| `worker/app/recommender.py` | Recommendation generation |
| `mcp/app/server.py` | MCP tools for AI agents |
| `postgres/init.sql` | Database schema |

## Common Tasks

### Check API Health

```bash
curl http://localhost:19000/healthz
```

### Get Auth Token

```bash
curl -X POST http://localhost:19000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"changeme"}'
```

### Trigger Indexing

```bash
TOKEN="your_token"

curl -X POST http://localhost:19000/api/vibe/index-all \
  -H "Authorization: Bearer $TOKEN"
```

### Check MCP with Inspector

```bash
npx @modelcontextprotocol/inspector http://localhost:8090/mcp
```

### Access Database

```bash
docker compose exec postgres psql -U kwhale -d kwhale
```

### Run Tests

```bash
# API tests
docker compose exec api pytest

# Worker tests
docker compose exec worker pytest
```

## Adding a New Source Plugin

Source plugins allow KWhale to search and download from streaming services.

1. Create `worker/app/providers/my_source.py`
2. Subclass `BaseProvider` from `.base`
3. Implement required methods:
   - `search(query: str) -> list[dict]`
   - `resolve(provider_id: str) -> dict`
   - `download(provider_id: str, output_path: str) -> str`
4. Set `name = "my_source"` on the class
5. Rebuild worker: `docker compose up -d --build worker`

No registration needed — the registry auto-discovers plugins by scanning the `providers/` directory.

Example:
```python
from .base import BaseProvider

class MySourceProvider(BaseProvider):
    name = "my_source"
    
    async def search(self, query: str, limit: int = 10) -> list[dict]:
        # Search implementation
        return [{"id": "...", "title": "...", "artist": "..."}]
    
    async def resolve(self, provider_id: str) -> dict:
        # Get track details
        return {"id": provider_id, "title": "...", "download_url": "..."}
    
    async def download(self, provider_id: str, output_path: str) -> str:
        # Download track to output_path
        return output_path
```

## Environment Variables

### Root `.env`
- `DATA_ROOT` - Data directory path
- `POSTGRES_PASSWORD` - PostgreSQL password
- `NAVIDROME_USERNAME` - Navidrome admin username
- `NAVIDROME_PASSWORD` - Navidrome admin password

### `api/.env` and `worker/.env`
- `DATABASE_URL` - PostgreSQL connection string
- `REDIS_URL` - Redis connection string
- `OPENAI_API_KEY` - OpenAI API key (or compatible service)
- `OPENAI_API_BASE` - API endpoint URL
- `LLM_MODEL` - Model for agent (default: deepseek-v4-flash)
- `OMNI_MODEL` - Model for spectrogram analysis (default: mimo-v2-omni)
- `ICM_PARTNER_KEY` - ICM API key (optional)
- `YANDEX_MUSIC_TOKEN` - Yandex Music token (optional)

See [CONFIGURATION.md](CONFIGURATION.md) for complete reference.

## Database Schema

The database uses PostgreSQL 16 with pgvector extension.

Key tables:
- `track_features` - Audio features, embeddings, vibe tags
- `playback_events` - Rich telemetry from clients
- `recommendations` - Generated recommendation lists
- `download_queue` - Track download tasks
- `taste_profile` - User preference profiles

See `postgres/init.sql` for complete schema.

## API Documentation

Interactive API docs available at:
- Swagger UI: http://localhost:19000/docs
- ReDoc: http://localhost:19000/redoc
- OpenAPI JSON: http://localhost:19000/openapi.json

See [API.md](API.md) for detailed endpoint documentation.

## Testing

### Running Tests

```bash
# Run all API tests
docker compose exec api pytest -v

# Run all worker tests
docker compose exec worker pytest -v

# Run specific test file
docker compose exec api pytest tests/test_prompt_agent.py -v

# Run with coverage
docker compose exec worker pytest --cov=app tests/
```

### Writing Tests

Tests use pytest with fixtures defined in `conftest.py`.

Example:
```python
import pytest

@pytest.mark.asyncio
async def test_search_library(db_pool):
    # Test implementation
    pass
```

## Code Style

- Python: Follow PEP 8
- Use type hints for function signatures
- Docstrings for public functions and classes
- Keep functions focused and small
- Use async/await for I/O operations

## Debugging

### View Logs

```bash
# All services
docker compose logs -f

# Specific service
docker compose logs -f api
docker compose logs -f worker

# Last 100 lines
docker compose logs --tail=100 worker
```

### Access Container Shell

```bash
docker compose exec api bash
docker compose exec worker bash
```

### Check Resource Usage

```bash
docker stats
```

## Contributing

See [CONTRIBUTING.md](../CONTRIBUTING.md) for contribution guidelines.

## Useful Links

- [Architecture Documentation](../ARCHITECTURE.md)
- [API Reference](API.md)
- [Configuration Guide](CONFIGURATION.md)
- [Installation Guide](INSTALLATION.md)
