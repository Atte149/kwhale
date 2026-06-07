# KWhale — Launch Guide

Run these commands in order. All are safe and reversible.
They don't touch the existing musicbrain stack.

## Step 1 — Copy project to /files and create data dirs

```bash
# Copy project files
cp -r ~/kwhale /files/kwhale

# Create data directories
mkdir -p /files/kwhale/data/music/library
mkdir -p /files/kwhale/data/music/incoming
mkdir -p /files/kwhale/data/music/failed
mkdir -p /files/kwhale/data/navidrome
mkdir -p /files/kwhale/data/postgres
mkdir -p /files/kwhale/data/redis
```

## Step 2 — Add ICM key (if you have one)

Edit `/files/kwhale/api/.env` and `/files/kwhale/worker/.env`:
```
ICM_PARTNER_KEY=your_key_here
```

Also add Yandex token if you have it:
```
YANDEX_MUSIC_TOKEN=your_token_here
```

## Step 3 — Build and start

```bash
cd /files/kwhale
docker compose build
docker compose up -d
```

Build takes ~5-10 min first time (Essentia compilation in worker).

## Step 4 — Set up Navidrome (first launch only)

1. Open http://localhost:4535 in browser
2. Create admin user: username `vladik`, password `melorise`
   (matches the `.env` values already set)

## Step 5 — Point at existing music library

**Option A (recommended): symlink into data dir**
```bash
ln -s /files/musicbrain/data/music/library/* /files/kwhale/data/music/library/
```

**Option B: copy (safe, uses disk space)**
```bash
cp -r /files/musicbrain/data/music/library/. /files/kwhale/data/music/library/
```

**Option C: just mount the same path** — edit docker-compose.yml, change:
```yaml
- ${DATA_ROOT}/music/library:/music:ro
```
to:
```yaml
- /files/musicbrain/data/music/library:/music:ro
```
Then `docker compose up -d navidrome`.

Navidrome auto-scans every minute and will pick up the library.

## Step 6 — Verify

```bash
# API health
curl http://localhost:19000/healthz

# Get token
curl -X POST http://localhost:19000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"vladik","password":"melorise"}'

# Open docs
# http://localhost:19000/docs
```

## Step 7 — Start indexing

```bash
TOKEN="your_token_from_step_6"

curl -X POST http://localhost:19000/api/vibe/index-all \
  -H "Authorization: Bearer $TOKEN"
```

This runs in background. Essentia takes ~2-5 sec per track.
A library of 2000 tracks = ~2-3 hours.

## Step 8 — Verify MCP

```bash
npx @modelcontextprotocol/inspector http://localhost:8090/mcp
```

Should show 11 tools: search_library, get_similar_tracks, search_sources, etc.

---

## Stopping

```bash
cd /files/kwhale
docker compose down
```

This does NOT delete your data (it's in /files/kwhale/data/).

## Troubleshooting

### Worker won't start
Check `docker compose logs worker`. Most likely: Essentia build failed or DATABASE_URL wrong.

### Navidrome doesn't see tracks
Trigger manual scan: Navidrome UI → Settings → Scan Library

### API returns 401
Token expired (48h). Re-login at POST /auth/login.

### Recommendations empty
Need playback events first. Use the app for a while, then POST /recs/generate.
