# KWhale Configuration Guide

Complete reference for configuring KWhale services.

## Environment Files

KWhale uses multiple environment files:

- `.env` — Root configuration (Docker Compose variables)
- `api/.env` — API service configuration
- `worker/.env` — Worker service configuration
- `mcp/.env` — MCP server configuration

## Root Configuration (`.env`)

### Required Variables

```bash
# Data directory (all volumes mount from here)
DATA_ROOT=./data

# PostgreSQL password
POSTGRES_PASSWORD=your_secure_password_here

# Navidrome credentials
NAVIDROME_USERNAME=admin
NAVIDROME_PASSWORD=changeme
```

### Database URL

Automatically constructed from other variables:
```bash
DATABASE_URL=postgresql://kwhale:${POSTGRES_PASSWORD}@postgres:5432/kwhale
```

### Optional Variables

```bash
# Yandex Music token for Yandex source plugin
YANDEX_MUSIC_TOKEN=
```

## API Configuration (`api/.env`)

### Required Variables

```bash
# Database connection
DATABASE_URL=postgresql://kwhale:password@postgres:5432/kwhale

# Redis connection
REDIS_URL=redis://redis:6379/0

# JWT secret for authentication (generate with: openssl rand -hex 32)
JWT_SECRET=your_random_secret_here

# OpenAI-compatible API for LLM features
OPENAI_API_KEY=sk-your-key-here
OPENAI_API_BASE=https://api.openai.com/v1
```

### Optional Variables

```bash
# LLM model for prompt agent (default: gpt-4)
LLM_MODEL=gpt-4

# JWT token expiration in hours (default: 48)
JWT_EXPIRATION_HOURS=48

# ICM partner key for ICM source plugin
ICM_PARTNER_KEY=

# Embedding service URL (default: http://embedding:8000/v1/embeddings)
EMBEDDING_API_URL=http://embedding:8000/v1/embeddings

# Navidrome connection (usually set via docker-compose environment)
NAVIDROME_URL=http://navidrome:4533
NAVIDROME_USERNAME=admin
NAVIDROME_PASSWORD=changeme
```

## Worker Configuration (`worker/.env`)

### Required Variables

```bash
# Database connection
DATABASE_URL=postgresql://kwhale:password@postgres:5432/kwhale

# Redis connection
REDIS_URL=redis://redis:6379/0

# OpenAI-compatible API for LLM features
OPENAI_API_KEY=sk-your-key-here
OPENAI_API_BASE=https://api.openai.com/v1
```

### Optional Variables

```bash
# LLM models
LLM_MODEL=gpt-4                    # For vibe tags
OMNI_MODEL=gpt-4-vision-preview    # For spectrogram analysis

# LLM retry configuration
LLM_MAX_ATTEMPTS=4                 # Max retry attempts
LLM_RETRY_BASE_DELAY=1.0          # Base delay in seconds
LLM_RETRY_MAX_DELAY=20.0          # Max delay in seconds

# Source plugin credentials
ICM_PARTNER_KEY=
YANDEX_MUSIC_TOKEN=

# Timezone offset for time-of-day features (hours from UTC)
LOCAL_TZ_OFFSET_HOURS=0

# Celery configuration
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/0

# Embedding service
EMBEDDING_API_URL=http://embedding:8000/v1/embeddings

# Navidrome connection
NAVIDROME_URL=http://navidrome:4533
NAVIDROME_USERNAME=admin
NAVIDROME_PASSWORD=changeme
```

## MCP Configuration (`mcp/.env`)

### Required Variables

```bash
# Database connection
DATABASE_URL=postgresql://kwhale:password@postgres:5432/kwhale
```

### Optional Variables

```bash
# Navidrome connection
NAVIDROME_URL=http://navidrome:4533
NAVIDROME_USERNAME=admin
NAVIDROME_PASSWORD=changeme

# KWhale API URL
KWHALE_API_URL=http://api:8000
```

## Docker Compose Variables

Set in root `.env` file, used by `docker-compose.yml`:

```bash
# Data root for all volumes
DATA_ROOT=./data

# PostgreSQL password
POSTGRES_PASSWORD=secure_password

# Navidrome credentials
NAVIDROME_USERNAME=admin
NAVIDROME_PASSWORD=changeme

# Yandex Music token (optional)
YANDEX_MUSIC_TOKEN=
```

## Service-Specific Configuration

### Navidrome

Configured via environment variables in `docker-compose.yml`:

```yaml
environment:
  ND_SCANSCHEDULE: 1m              # Scan interval
  ND_LOGLEVEL: info                # Log level
  ND_SESSIONTIMEOUT: 24h           # Session timeout
  ND_LASTFM_ENABLED: "false"       # Last.fm scrobbling
  ND_ENABLETRANSCODINGCONFIG: "true"
  ND_DEFAULTTHEME: "Dark"
```

### PostgreSQL

```yaml
environment:
  POSTGRES_DB: kwhale
  POSTGRES_USER: kwhale
  POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
```

### Embedding Service

```yaml
environment:
  EMBEDDING_MODEL: BAAI/bge-m3     # Model to use
```

## Feature Flags

### Enabling Source Plugins

Source plugins are enabled by providing their credentials:

**ICM**: Set `ICM_PARTNER_KEY` in `api/.env` and `worker/.env`
**Yandex**: Set `YANDEX_MUSIC_TOKEN` in root `.env`

### Disabling LLM Features

To run without LLM features:
- Leave `OPENAI_API_KEY` empty
- Vibe tags and prompt agent will be disabled
- Recommendations will still work (ALS + content-based)

## Security Considerations

### Secrets Management

- Never commit `.env` files to git (already in `.gitignore`)
- Use strong, unique passwords
- Rotate JWT secrets periodically
- Keep API keys secure

### JWT Secret Generation

```bash
openssl rand -hex 32
```

### Network Security

Default configuration binds services to `127.0.0.1` (localhost only):

```yaml
ports:
  - "127.0.0.1:19000:8000"  # API
  - "127.0.0.1:4535:4533"   # Navidrome
  - "127.0.0.1:8090:8090"   # MCP
```

To expose externally, use a reverse proxy (Caddy, nginx) with HTTPS.

## Performance Tuning

### Worker Concurrency

```yaml
command: ["celery", "-A", "app.tasks.celery_app", "worker", 
          "--loglevel=INFO", "--concurrency=4"]
```

Adjust `--concurrency` based on CPU cores.

### Resource Limits

```yaml
deploy:
  resources:
    limits:
      cpus: "2.0"
      memory: 4G
```

Adjust based on available resources:
- Embedding service needs 4GB minimum
- Worker benefits from more CPU for Essentia

### Database Connection Pool

Set in `DATABASE_URL`:
```bash
DATABASE_URL=postgresql://kwhale:password@postgres:5432/kwhale?pool_size=20&max_overflow=10
```

## Logging

### Log Levels

Set via environment:
```bash
# API
LOG_LEVEL=INFO

# Worker
CELERY_LOG_LEVEL=INFO

# Navidrome
ND_LOGLEVEL=info
```

Levels: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`

### Viewing Logs

```bash
# All services
docker compose logs -f

# Specific service
docker compose logs -f worker

# Last 100 lines
docker compose logs --tail=100 api
```

## Backup and Restore

### Backup

```bash
# Database
docker compose exec postgres pg_dump -U kwhale kwhale > backup.sql

# Data directory
tar -czf data-backup.tar.gz ./data
```

### Restore

```bash
# Database
docker compose exec -T postgres psql -U kwhale kwhale < backup.sql

# Data directory
tar -xzf data-backup.tar.gz
```

## Troubleshooting

### Connection Issues

Check service connectivity:
```bash
docker compose exec api ping postgres
docker compose exec worker ping redis
```

### Database Issues

Access database:
```bash
docker compose exec postgres psql -U kwhale -d kwhale
```

Check connections:
```sql
SELECT * FROM pg_stat_activity;
```

### Memory Issues

Check resource usage:
```bash
docker stats
```

Reduce memory limits if needed or increase available RAM.

## Advanced Configuration

### Custom Navidrome Configuration

Mount custom config:
```yaml
volumes:
  - ./navidrome.toml:/data/navidrome.toml
```

### External PostgreSQL

Use external database:
```bash
DATABASE_URL=postgresql://user:pass@external-host:5432/kwhale
```

Remove `postgres` service from `docker-compose.yml`.

### External Redis

```bash
REDIS_URL=redis://external-host:6379/0
```

Remove `redis` service from `docker-compose.yml`.

## Environment Variable Reference

See `.env.example`, `api/.env.example`, and `worker/.env.example` for complete lists with descriptions.
