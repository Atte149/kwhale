# KWhale Installation Guide

This guide walks you through setting up KWhale on your server.

## Prerequisites

- Docker and Docker Compose installed
- At least 8GB RAM (4GB for embedding service, rest for other services)
- 10GB+ disk space for data
- (Optional) Existing music library to import

## Quick Start

### 1. Clone and Configure

```bash
git clone https://github.com/yourusername/kwhale.git
cd kwhale

# Copy environment template
cp .env.example .env

# Edit with your settings
nano .env
```

Set these required values in `.env`:
- `DATA_ROOT` - Where all data will be stored (default: `./data`)
- `POSTGRES_PASSWORD` - Secure password for PostgreSQL
- `NAVIDROME_USERNAME` - Admin username for Navidrome
- `NAVIDROME_PASSWORD` - Admin password for Navidrome

### 2. Configure API Keys

```bash
# Copy API environment templates
cp api/.env.example api/.env
cp worker/.env.example worker/.env

# Edit with your API keys
nano api/.env
nano worker/.env
```

Required for AI features:
- `OPENAI_API_KEY` - For LLM-powered recommendations and vibe tags
- `OPENAI_API_BASE` - API endpoint (default: OpenAI, or use compatible service)

Optional:
- `ICM_PARTNER_KEY` - For ICM music source plugin
- `YANDEX_MUSIC_TOKEN` - For Yandex Music source plugin

### 3. Create Data Directories

```bash
mkdir -p data/music/{library,incoming,failed}
mkdir -p data/{navidrome,postgres,redis}
```

### 4. Build and Start

```bash
docker compose build
docker compose up -d
```

First build takes 5-10 minutes (compiles Essentia audio analysis library).

### 5. Verify Services

```bash
# Check all services are running
docker compose ps

# Check API health
curl http://localhost:19000/healthz

# View logs
docker compose logs -f api
```

### 6. Set Up Navidrome (First Launch Only)

1. Open http://localhost:4535 in your browser
2. Create admin user with the credentials from your `.env` file
3. Navidrome will start scanning your music library automatically

### 7. Add Your Music Library

**Option A: Copy files into data directory**
```bash
cp -r /path/to/your/music/* ./data/music/library/
```

**Option B: Mount existing library**

Edit `docker-compose.yml` and change the navidrome volumes section:
```yaml
volumes:
  - /path/to/your/music:/music:ro
```

Then restart:
```bash
docker compose up -d navidrome
```

Navidrome scans every minute and will pick up new files automatically.

### 8. Get API Token

```bash
curl -X POST http://localhost:19000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"your_password"}'
```

Save the returned token - you'll need it for API calls.

### 9. Start Indexing

```bash
TOKEN="your_token_from_step_8"

curl -X POST http://localhost:19000/api/vibe/index-all \
  -H "Authorization: Bearer $TOKEN"
```

This runs in the background. Essentia audio analysis takes ~2-5 seconds per track.
A library of 2000 tracks takes ~2-3 hours.

Check progress:
```bash
docker compose logs -f worker
```

### 10. Verify MCP Server (Optional)

If you want to use the MCP server with AI agents:

```bash
npx @modelcontextprotocol/inspector http://localhost:8090/mcp
```

Should show 11 tools: search_library, get_similar_tracks, search_sources, etc.

## Port Reference

| Service | Port | Access |
|---------|------|--------|
| KWhale API | 19000 | http://localhost:19000 |
| Navidrome | 4535 | http://localhost:4535 |
| MCP Server | 8090 | http://localhost:8090 |
| Embedding Service | 8095 | Internal only |
| PostgreSQL | 5432 | Internal only |
| Redis | 6379 | Internal only |

## Data Directories

All data is stored under `DATA_ROOT` (default: `./data`):

```
data/
├── music/
│   ├── library/      # Organized music library (Navidrome reads this)
│   ├── incoming/     # Download landing zone (tagger watches this)
│   └── failed/       # Files tagger couldn't identify
├── navidrome/        # Navidrome state (SQLite DB, config)
├── postgres/         # PostgreSQL data
└── redis/            # Redis persistence
```

## Stopping and Restarting

```bash
# Stop all services
docker compose down

# Stop and remove volumes (WARNING: deletes all data)
docker compose down -v

# Restart a single service
docker compose restart api

# Rebuild after code changes
docker compose up -d --build api
```

## Troubleshooting

### Worker won't start
Check logs: `docker compose logs worker`

Common causes:
- Essentia build failed (check for compilation errors)
- DATABASE_URL incorrect in worker/.env
- Missing required environment variables

### Navidrome doesn't see tracks
- Trigger manual scan: Navidrome UI → Settings → Scan Library
- Check volume mounts: `docker compose exec navidrome ls /music`
- Verify file permissions (Navidrome needs read access)

### API returns 401 Unauthorized
- Token expired (48h lifetime)
- Re-login: `POST /api/auth/login`

### Recommendations are empty
- Need playback events first - use the app for a while
- Manually trigger: `POST /api/recs/generate` with your token
- Check indexing progress: `docker compose logs worker`

### Embedding service out of memory
- Requires 4GB RAM minimum
- Reduce Docker memory limits if needed
- Check: `docker stats`

### LLM features not working
- Verify `OPENAI_API_KEY` is set in api/.env and worker/.env
- Check `OPENAI_API_BASE` points to a valid endpoint
- View errors: `docker compose logs api` or `docker compose logs worker`

## Next Steps

- Read [API.md](API.md) for complete API reference
- See [DEVELOPMENT.md](DEVELOPMENT.md) for development setup
- Check [CONFIGURATION.md](CONFIGURATION.md) for advanced configuration
- Review [ARCHITECTURE.md](../ARCHITECTURE.md) to understand the system design

## Updating

```bash
git pull
docker compose build
docker compose up -d
```

Database migrations run automatically on startup.
