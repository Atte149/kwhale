# KWhale — Architecture

## Overview

KWhale is a self-hosted music service built around a single design principle:
**keep "commodity" infrastructure as black boxes and own only the smart layer.**

```
┌──────────────────────────────────────────────────────────────────┐
│  Navic (forked Flutter client)                                    │
│  home widgets · BT now-playing tag · rich telemetry              │
└──────────────┬───────────────────────────────────────────────────┘
               │ ONE API  (Bearer token, all JSON)
               │ kwhale-api on :19000
┌──────────────▼───────────────────────────────────────────────────┐
│  FastAPI  "kwhale-api"                                            │
│                                                                   │
│  /library/*   →  proxies Navidrome (browse/search/cover)         │
│  /stream/*    →  302-redirect to Navidrome stream URL            │
│  /events      →  ingest rich playback telemetry                  │
│  /recs        →  recommendation feed                             │
│  /discover    →  search remote sources via plugins               │
│  /acquire     →  queue download job                              │
│  /vibe/{id}   →  Essentia features + vibe tags                  │
│  /mcp         →  FastMCP proxy (for clients that speak MCP)      │
└──┬──────────────────────────────┬────────────────────────────────┘
   │ internal HTTP                │ SQL
   │                              │
┌──▼──────────────────┐  ┌───────▼───────────────────────────────┐
│  Navidrome :4535    │  │  PostgreSQL :5432 (kwhale db)        │
│  media engine       │  │  + pgvector extension                  │
│  - filesystem scan  │  │  track_features, playback_events,      │
│  - transcoding      │  │  recommendations, download_queue,      │
│  - range streaming  │  │  source_cache, plugin_registry         │
│  - OpenSubsonic API │  └────────────────────────────────────────┘
│  BLACK BOX           │           │
└─────────────────────┘  ┌────────▼───────────────────────────────┐
         │reads library/  │  Worker (Celery)                       │
         │               │  - Source plugins: ICM, Yandex, Deezer │
┌────────▼──────────┐    │  - Essentia audio feature extraction   │
│  /data/library/   │◄───┤  - Lyrics fetch + text embeddings      │
│  organized files  │    │  - LLM vibe tags                       │
└────────────────────┘   │  - ALS + content recommendations       │
                          │  - Auto-tagging pipeline               │
                          └────────────────────────────────────────┘
                                      │ Redis :6379 (broker)
                          ┌───────────▼────────────────────────────┐
                          │  MCP Server (FastMCP) :8090            │
                          │  Tools for AI agents:                  │
                          │  - search, recommend, acquire          │
                          │  - get_taste_profile, log_event        │
                          │  - discover_sources, playlist_mood     │
                          └────────────────────────────────────────┘
```

## Design Decisions

### Why Navidrome stays a black box
Navidrome handles filesystem scanning, tag parsing for 20+ formats, range-request
streaming, on-the-fly ffmpeg transcoding, cover art resizing, and years of edge-case
fixes. Writing this in Python would be thousands of lines of fragile code. We use it
as an internal microservice and never touch its codebase.

### Why ONE API for the client (topology B)
The client only knows one address. FastAPI proxies the "boring" Navidrome calls and
enriches them with our smart data (vibe tags, recommendation score, etc.) in the same
response. The client never speaks Subsonic directly — so we are not limited by
Subsonic's field set.

### Why NOT fork the server (Koel, Black Candy, etc.)
Three languages (PHP/Ruby/Go) + our Python brain = unmaintainable for a vibe-coder.
The work we want to OWN (recommendations, telemetry, source plugins, MCP) is all
Python. We fork only the Flutter client, which is the thinnest possible surface area.

### Source Plugin System
Each streaming source (ICM, Yandex, Deezer, etc.) implements a `BaseProvider` ABC
with `search()`, `resolve()`, `download()`. The worker discovers providers by scanning
`worker/providers/` — adding a new source = adding one file.

### Telemetry vs Scrobble
Subsonic scrobble is a binary "played" ping. We capture structured events:
`{track_id, event_type, position_sec, duration_sec, completion_pct, skipped, ts}`
from the client's audio position stream and batch-POST to `/events`. This powers
real recommendations, skip-pattern analysis, and time-of-day listening context.

### MCP Transport
Both StreamableHTTP (for external AI agents) and stdio (for local Claude Desktop).
Single FastMCP server consolidating all tools.

## Port Map

| Service         | Port  | Exposed |
|-----------------|-------|---------|
| kwhale-api      | 19000 | Yes (Caddy) |
| Navidrome       | 4535  | Internal only |
| PostgreSQL      | 5432  | Internal only |
| Redis           | 6379  | Internal only |
| MCP server      | 8090  | localhost only |
| Tagger          | 8093  | Internal only |

## Data Paths (all under /files/kwhale/data)

| Path | Purpose |
|------|---------|
| `data/music/library/` | Organized music library (Navidrome reads this) |
| `data/music/incoming/` | Download landing zone (tagger watches this) |
| `data/music/failed/` | Files tagger couldn't identify |
| `data/navidrome/` | Navidrome state (SQLite DB, config) |
| `data/postgres/` | PostgreSQL data |
| `data/redis/` | Redis persistence |

## Feature Checklist

- [x] Local library streaming (via Navidrome)
- [x] Remote source discovery (via source plugins)  
- [x] Auto-download + auto-tagging pipeline
- [x] Essentia audio features (BPM, energy, valence, danceability, key, MFCC)
- [x] Text/lyrics embeddings (bge-m3 via OpenAI-compatible API)
- [x] LLM vibe tags
- [x] ALS collaborative filtering + content-based hybrid recommendations
- [x] Rich playback telemetry (duration, completion %, skips, seeks, time-of-day)
- [x] Home-screen widgets (client-side, via home_widget)
- [x] Bluetooth/AVRCP now-playing metadata (client-side, via audio_service)
- [x] Full MCP server for AI agents (FastMCP, streamable-http)
- [x] API documentation for agents and clients
