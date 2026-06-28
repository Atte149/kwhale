# 🐋 KWhale

**Self-hosted music streaming with AI-powered recommendations**

[![CI](https://github.com/Atte149/kwhale/actions/workflows/ci.yml/badge.svg)](https://github.com/Atte149/kwhale/actions/workflows/ci.yml)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Docker](https://img.shields.io/badge/docker-ready-blue.svg?logo=docker)](https://docs.docker.com/compose/)

KWhale combines [Navidrome](https://github.com/navidrome/navidrome) with an AI
recommendation engine, source plugins, and a beautiful Flutter mobile client.
Stream your library, get personalized recommendations based on audio features
and listening patterns, discover new music from multiple streaming services,
and organize your collection — all running on your own infrastructure.

## ✨ Features

- **Smart Recommendations** — Hybrid content-based + personalized scoring with
  pgvector retrieval and LLM curation. Per-user taste profiles.
- **Audio Analysis** — Essentia-powered feature extraction (BPM, energy,
  valence, key) + bge-m3 lyrics embeddings for semantic search.
- **Source Plugins** — Search and download from streaming services (ICM,
  Yandex Music). Extensible plugin system.
- **Auto-Tagging** — Automatic metadata resolution via Shazam + AcoustID.
  Smart library retagging with quality classification.
- **Artist Tools** — Split merged artist tags, transliterate Latin → Cyrillic
  folder names, enrich artist cards with streaming tracks.
- **Multi-Tenant** — Isolated libraries per user via Navidrome Multi-Library.
  Each client sees only their own music.
- **Library Import** — Paste a track list (from Spotify, Apple Music, CSV) and
  KWhale downloads missing tracks automatically.
- **MCP Server** — AI agent integration via Model Context Protocol.
- **OpenSubsonic Compatible** — Works with existing Subsonic clients.

## 🚀 Quick Start

### One-command install

```bash
curl -fsSL https://raw.githubusercontent.com/Atte149/kwhale/main/install.py | python3 -
```

The installer will:
1. Clone the repo and generate all `.env` files
2. Create data directories and Docker networks
3. Build and start all services
4. Wait for health checks
5. Print your API token and next steps

### Manual install

```bash
git clone https://github.com/Atte149/kwhale.git
cd kwhale

# Configure environment
cp .env.example .env       # edit with your passwords
cp api/.env.example api/.env
cp worker/.env.example worker/.env
cp mcp/.env.example mcp/.env

# Create data dirs
mkdir -p kwhale-data/{music/library,music/incoming,navidrome,postgres,redis}

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

KWhale has a Flutter Android client (fork of [Musly](https://github.com/dddevid/Musly)):

```bash
# Download the latest APK from your server:
curl -O http://localhost:19000/download/latest
```

Or build from source:
```bash
cd kwhale-client
flutter build apk --release
```

**Client repo:** [Atte149/kwhale-client](https://github.com/Atte149/kwhale-client)

## 🏗 Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  Client (Flutter app, Subsonic clients)                          │
└──────────────┬───────────────────────────────────────────────────┘
               │ HTTP/JSON
┌──────────────▼───────────────────────────────────────────────────┐
│  FastAPI (kwhale-api) :19000                                      │
│  /library/*  → proxy to Navidrome (per-user library isolation)   │
│  /recs       → personalized recommendations                       │
│  /discover   → search remote sources + acquire                   │
│  /vibe/{id}  → audio features + vibe tags                        │
└──┬──────────────────────────────┬────────────────────────────────┘
   │ Subsonic API                 │ SQL
┌──▼──────────────────┐  ┌───────▼───────────────────────────────┐
│  Navidrome :4535    │  │  PostgreSQL 16 + pgvector              │
│  Multi-Library      │  │  track_features, playback_events,     │
│  Per-user access    │  │  recommendations, artist_aliases      │
└─────────────────────┘  └────────┬──────────────────────────────┘
          │                       │
┌─────────▼──────────┐  ┌────────▼──────────────────────────────┐
│  Music Library     │  │  Worker (Celery)                       │
│  /music/<user>/    │  │  Essentia · bge-m3 · LLM vibe tags     │
└────────────────────┘  │  Recommender · Source plugins          │
                        │  Retagging · Artist split · Translit   │
                        └────────────────────────────────────────┘
```

## 🛠 Tech Stack

| Component | Technology |
|-----------|-----------|
| API | FastAPI, Python 3.11+ |
| Media Engine | Navidrome (Go) |
| Database | PostgreSQL 16 + pgvector |
| Task Queue | Celery + Redis |
| Audio Analysis | Essentia |
| Embeddings | bge-m3 (sentence-transformers) |
| LLM | OpenAI-compatible API (configurable) |
| Client | Flutter (Android) |
| MCP | FastMCP |

## 🔌 Source Plugins

KWhale can search and download from multiple streaming services:

- **ICM** (Internet Content Music) — preferred, high-quality
- **Yandex Music** — fallback, broad catalog
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
picks). Per-user, multi-tenant aware.

## 📚 Documentation

- [Installation Guide](docs/INSTALLATION.md) — Complete setup walkthrough
- [Architecture](ARCHITECTURE.md) — System design and components
- [Configuration](docs/CONFIGURATION.md) — Environment variables reference
- [Development](docs/DEVELOPMENT.md) — Contributing and local development

## 🤝 Contributing

Contributions are welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## 📄 License

KWhale is licensed under the [GNU General Public License v3](LICENSE).

The KWhale client (Flutter app) is a fork of [Musly](https://github.com/dddevid/Musly)
by [dddevid](https://github.com/dddevid), used under its license.

## 💬 Community

- [GitHub Issues](https://github.com/Atte149/kwhale/issues) — Bug reports
- [GitHub Discussions](https://github.com/Atte149/kwhale/discussions) — Ideas, Q&A