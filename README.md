# 🐋 KWhale

**Self-hosted music streaming with AI-powered recommendations**

[![Docker](https://img.shields.io/badge/docker-ready-blue.svg?logo=docker)](https://docs.docker.com/compose/)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)

KWhale combines [Gonic](https://github.com/sentriz/gonic) (a lightweight Subsonic
server) with an AI recommendation engine, source plugins, and a native Android
client built on [PixelPlayer](https://github.com/PixelPlayerHQ/PixelPlayer).

Stream your library, get personalized recommendations based on audio features
and listening patterns, discover new music from multiple streaming services,
and organize your collection — all running on your own infrastructure.

## ✨ Features

- **Smart Recommendations** — Hybrid content-based + personalized scoring with
  pgvector retrieval and LLM curation. Per-user taste profiles.
- **Audio Analysis** — Essentia-powered feature extraction (BPM, energy,
  valence, key) + bge-m3 lyrics embeddings for semantic search.
- **Source Plugins** — Search and download from streaming services (ICM,
  Yandex Music, SoundCloud). Extensible plugin system.
- **Auto-Tagging** — Automatic metadata resolution via Shazam + AcoustID.
  Smart library retagging with quality classification.
- **Multi-Value Artist Tags** — Native multi-value ARTIST tag support
  (FLAC Vorbis, MP3 ID3v2.4) with proper artist splitting.
- **Artist Tools** — Split merged artist tags, transliterate Latin → Cyrillic,
  enrich artist cards with streaming tracks.
- **Multi-User** — Isolated libraries per user via Gonic.
- **Library Import** — Paste a track list and KWhale downloads missing tracks
  automatically.
- **MCP Server** — AI agent integration via Model Context Protocol.
- **Subsonic Compatible** — Works with existing Subsonic clients via Gonic.

## 🚀 Quick Start

### One-command install

```bash
curl -fsSL https://raw.githubusercontent.com/Atte149/kwhale/main/install.py | python3 -
```

The installer will:
1. Clone the repo and generate all `.env` files
2. Create data directories and Docker networks
3. Build and start all services (Gonic, API, Worker, PostgreSQL, Redis)
4. Wait for health checks
5. Print your API token and next steps

### Manual install

```bash
git clone https://github.com/Atte149/kwhale.git
cd kwhale

# Configure environment
cp .env.example .env       # edit with your passwords

# Create data dirs
mkdir -p kwhale-data/{music/library,music/incoming,gonic,postgres,redis}

# Build and start
docker compose build
docker compose up -d

# Verify
curl http://localhost:19000/healthz
```

### Prerequisites

- Docker + Docker Compose v2
- 8 GB+ RAM (embedding model + Essentia)
- 10 GB+ disk space

## 📱 Mobile Client

KWhale has a native Android client built on PixelPlayer (Kotlin + Jetpack Compose +
Material 3 Expressive):

```bash
# Download the latest APK from releases
# https://github.com/Atte149/kwhale-client/releases
```

Or build from source:
```bash
cd kwhale-client
JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64 ./gradlew assembleRelease
# APK: app/build/outputs/apk/release/app-release.apk
```

**Client repo:** [Atte149/kwhale-client](https://github.com/Atte149/kwhale-client)

## 🏗 Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  Client (Android, Kotlin/Compose, Material 3 Expressive)         │
└──────────────┬───────────────────────────────────────────────────┘
               │ Subsonic API + KWhale REST API
┌──────────────▼───────────────────────────────────────────────────┐
│  Caddy (reverse proxy, TLS)                                       │
│  /rest/* → Gonic    /api/* → KWhale API                           │
└──────┬──────────────────────────┬────────────────────────────────┘
       │                          │
┌──────▼────────────┐  ┌──────────▼─────────────────────────────────┐
│  Gonic :80        │  │  FastAPI (kwhale-api) :19000               │
│  Subsonic server  │  │  /library/*  → proxy to Gonic              │
│  Multi-value tags │  │  /recs       → personalized recommendations │
│  Auto-scan watcher│  │  /discover   → search remote + acquire      │
│  Stream + covers  │  │  /vibe/{id}  → audio features + vibe tags  │
└───────────────────┘  └──┬────────────────────────────────────────┘
                          │ SQL
                   ┌──────▼────────────────────────────────────────┐
                   │  PostgreSQL 16 + pgvector                       │
                   │  track_features, playback_events,              │
                   │  recommendations, artist_aliases               │
                   └────────┬───────────────────────────────────────┘
                            │
┌───────────────────┐  ┌────▼───────────────────────────────────────┐
│  Music Library    │  │  Worker (Celery)                            │
│  /music/library/  │  │  Essentia · bge-m3 · LLM vibe tags          │
│                   │  │  Recommender · Source plugins               │
│                   │  │  Retagging · Artist split · Translit        │
│                   │  │  ICM key keepalive (12h)                    │
└───────────────────┘  └────────────────────────────────────────────┘
```

## 🛠 Tech Stack

| Component | Technology |
|-----------|-----------|
| API | FastAPI, Python 3.11+ |
| Media Engine | Gonic (Go) — Subsonic compatible |
| Database | PostgreSQL 16 + pgvector |
| Task Queue | Celery + Redis |
| Audio Analysis | Essentia |
| Embeddings | bge-m3 (sentence-transformers) |
| LLM | OpenAI-compatible API (configurable) |
| Client | Kotlin + Jetpack Compose (Android, Material 3 Expressive) |
| MCP | FastMCP |
| Reverse Proxy | Caddy |

## 🔌 Source Plugins

KWhale can search and download from multiple streaming services:

- **ICM** (Internet Content Music) — Apple Music catalog, high-quality
- **Yandex Music** — broad Russian catalog
- **SoundCloud** — independent artists
- **Extensible** — add your own in `worker/app/providers/`

## 🧠 Recommendation Engine

KWhale uses a three-layer recommendation system:

1. **Candidate Generation** — pgvector cosine kNN from the user's taste centroid
   with HNSW index (`ef_search=300` for high recall)
2. **Personalization Layer** — play frequency, completion rate, time-of-day
   affinity, recency decay, skip penalty
3. **LLM Curation** — one call to an LLM (configurable, defaults to a small
   model) that re-ranks candidates with transparent scoring

Recommendations are always persisted (cold-start fallback to random fresh
picks). Per-user aware.

## 🎵 Multi-Value Artist Tags

KWhale properly handles multi-artist tracks:

- **FLAC:** Multiple `ARTIST` Vorbis comment fields (one per artist)
- **MP3:** ID3v2.4 `TPE1` with null-separated values
- **Gonic:** Native multi-value tag support (`GONIC_MULTI_VALUE_ARTIST=multi`)
- **Bulk retag:** `worker/app/bulk_retag.py` — one-shot script to fix existing library
- **Translit:** `worker/app/translit.py` — Cyrillic/Latin artist name normalization

## 📚 Documentation

- [Installation Guide](docs/INSTALLATION.md) — Complete setup walkthrough
- [Architecture](ARCHITECTURE.md) — System design and components
- [Configuration](docs/CONFIGURATION.md) — Environment variables reference
- [Development](docs/DEVELOPMENT.md) — Contributing and local development
- [API Reference](docs/API.md) — REST API documentation

## 🛠 Management

```bash
python3 kwhalectl.py status          # show container status + health
python3 kwhalectl.py smoke            # end-to-end smoke test
python3 kwhalectl.py logs [service]  # tail logs
python3 kwhalectl.py restart [svc]   # restart service(s)
python3 kwhalectl.py scan             # trigger Gonic library scan
python3 kwhalectl.py index            # trigger AI indexing
python3 kwhalectl.py token            # get API token
python3 kwhalectl.py stop             # stop all services
python3 kwhalectl.py start            # start all services
python3 kwhalectl.py update           # git pull + rebuild + restart
```

## 🤝 Contributing

Contributions are welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## 📄 License

KWhale is licensed under the [GNU General Public License v3](LICENSE).

The KWhale client is a fork of [PixelPlayer](https://github.com/PixelPlayerHQ/PixelPlayer)
by [theovilardo](https://github.com/theovilardo), used under its license.

## 💬 Community

- [GitHub Issues](https://github.com/Atte149/kwhale/issues) — Bug reports
- [GitHub Discussions](https://github.com/Atte149/kwhale/discussions) — Ideas, Q&A