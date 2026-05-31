# KWhale

**Self-hosted music streaming service with AI-powered recommendations**

KWhale combines a robust media server with intelligent music discovery. Stream your library, get personalized recommendations based on audio features and listening patterns, and discover new music from multiple sources — all running on your own infrastructure.

## ✨ Key Features

- **Smart Recommendations** — Hybrid collaborative + content-based filtering with personalized scoring
- **Audio Analysis** — Essentia-powered feature extraction (BPM, energy, valence, key, timbre)
- **Semantic Search** — Find songs by lyric meaning using text embeddings
- **LLM-Powered Agent** — Natural language playlist generation with tool-calling
- **Rich Telemetry** — Track completion rates, skip patterns, and time-of-day preferences
- **Source Plugins** — Search and download from multiple streaming services (ICM, Yandex, Deezer)
- **Auto-Tagging** — Automatic file organization and metadata enrichment
- **MCP Server** — AI agent integration via Model Context Protocol
- **OpenSubsonic Compatible** — Works with existing Subsonic clients

## 🏗️ Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  Client (Flutter app, Subsonic clients)                          │
│  Bearer token authentication, JSON API                           │
└──────────────┬───────────────────────────────────────────────────┘
               │ HTTP/JSON
┌──────────────▼───────────────────────────────────────────────────┐
│  FastAPI (kwhale-api) :19000                                      │
│  /library/*   → proxy to Navidrome                               │
│  /stream/*    → 302 redirect to Navidrome                        │
│  /events      → ingest playback telemetry                        │
│  /recs        → personalized recommendations                     │
│  /discover    → search remote sources                            │
│  /vibe/{id}   → audio features + vibe tags                      │
└──┬──────────────────────────────┬────────────────────────────────┘
   │ internal HTTP                │ SQL
┌──▼──────────────────┐  ┌───────▼───────────────────────────────┐
│  Navidrome :4535    │  │  PostgreSQL + pgvector                 │
│  - Media streaming  │  │  - Track features & embeddings         │
│  - Transcoding      │  │  - Playback events                     │
│  - OpenSubsonic API │  │  - Recommendations                     │
└─────────────────────┘  │  - Download queue                      │
         │               └────────┬───────────────────────────────┘
         │ reads library          │
┌────────▼──────────┐    ┌───────▼────────────────────────────────┐
│  Music Library    │◄───┤  Worker (Celery)                       │
│  Organized files  │    │  - Essentia audio analysis             │
└───────────────────┘    │  - Text embeddings (bge-m3)            │
                         │  - LLM vibe tags                       │
                         │  - Recommendation generation           │
                         │  - Source plugins (download)           │
                         └────────────────────────────────────────┘
```

## 🚀 Quick Start

### Prerequisites
- Docker and Docker Compose
- 8GB+ RAM
- 10GB+ disk space

### Installation

```bash
# Clone repository
git clone https://github.com/yourusername/kwhale.git
cd kwhale

# Configure environment
cp .env.example .env
nano .env  # Set DATA_ROOT, passwords, credentials

# Set up API keys
cp api/.env.example api/.env
cp worker/.env.example worker/.env
nano api/.env  # Add OPENAI_API_KEY for AI features

# Run setup
bash scripts/setup.sh

# Build and start
docker compose build
docker compose up -d

# Verify
curl http://localhost:19000/healthz
```

### First Steps

1. **Set up Navidrome**: Open http://localhost:4535 and create admin account
2. **Add music**: Copy files to `./data/music/library/` or mount existing library
3. **Get API token**: `POST /api/auth/login` with your credentials
4. **Start indexing**: `POST /api/vibe/index-all` to analyze your library

See [docs/INSTALLATION.md](docs/INSTALLATION.md) for detailed setup instructions.

## 📚 Documentation

- [Installation Guide](docs/INSTALLATION.md) — Complete setup walkthrough
- [Architecture](ARCHITECTURE.md) — System design and components
- [API Reference](docs/API.md) — Complete API documentation
- [Development Guide](docs/DEVELOPMENT.md) — Contributing and development setup
- [Configuration](docs/CONFIGURATION.md) — Environment variables and options

## 🎯 How It Works

### Recommendation Engine

KWhale uses a three-layer recommendation system:

1. **Candidate Generation**
   - Collaborative filtering (ALS on playback history)
   - Content-based (audio feature similarity via pgvector)

2. **Personalization Layer**
   - Favorite tracks boost
   - Play frequency weighting
   - Completion rate scoring
   - Time-of-day affinity
   - Recency decay
   - Skip penalty

3. **LLM Agent** (optional)
   - Natural language playlist requests
   - Tool-calling with library search, semantic search, audio similarity
   - Validates all track IDs against actual library

### Audio Analysis

Each track is analyzed with:
- **Essentia**: BPM, energy, valence, danceability, key, mode, loudness, 20-dim MFCC vector
- **bge-m3**: 1024-dim text embedding of lyrics for semantic search
- **LLM**: Vibe tags (e.g., "melancholic", "driving", "late-night")
- **Multimodal**: Spectrogram description via vision model

## 🛠️ Tech Stack

- **API**: FastAPI, Python 3.11+
- **Media Engine**: Navidrome (Go)
- **Database**: PostgreSQL 16 + pgvector
- **Task Queue**: Celery + Redis
- **Audio Analysis**: Essentia
- **Embeddings**: bge-m3 (sentence-transformers)
- **LLM**: OpenAI-compatible API (configurable)
- **MCP**: FastMCP for AI agent integration

## 🔌 Source Plugins

KWhale can search and download from multiple streaming services:

- **ICM** (Internet Content Music)
- **Yandex Music**
- **Deezer**
- Extensible plugin system — add your own in `worker/app/providers/`

## 🤖 MCP Server

KWhale includes an MCP server for AI agent integration:

```bash
# Test with MCP Inspector
npx @modelcontextprotocol/inspector http://localhost:8090/mcp
```

Available tools:
- `search_library` — Search local library
- `get_similar_tracks` — Find similar by audio features
- `semantic_search` — Search by lyric meaning
- `get_recommendations` — Get personalized recommendations
- `acquire_track` — Download from remote sources
- `get_taste_profile` — User preference profile
- And more...

## 📊 API Endpoints

- `POST /api/auth/login` — Get JWT token
- `GET /api/library/search` — Search library
- `GET /api/stream/{id}` — Stream track (302 to Navidrome)
- `POST /api/events` — Ingest playback telemetry
- `GET /api/recs` — Get recommendations
- `GET /api/discover` — Search remote sources
- `POST /api/discover/acquire` — Download track
- `GET /api/vibe/{id}` — Get audio features

Interactive docs: http://localhost:19000/docs

## 🧪 Development

```bash
# Rebuild after code changes
docker compose up -d --build api

# View logs
docker compose logs -f worker

# Run tests
docker compose exec api pytest
docker compose exec worker pytest

# Access database
docker compose exec postgres psql -U kwhale -d kwhale
```

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for detailed development guide.

## 🤝 Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## 📄 License

MIT License - see [LICENSE](LICENSE) for details.

## 🙏 Acknowledgments

- [Navidrome](https://www.navidrome.org/) — Excellent media server
- [Essentia](https://essentia.upf.edu/) — Audio analysis library
- [pgvector](https://github.com/pgvector/pgvector) — Vector similarity search
- [FastMCP](https://github.com/jlowin/fastmcp) — MCP server framework

## 📞 Support

- **Issues**: [GitHub Issues](https://github.com/yourusername/kwhale/issues)
- **Discussions**: [GitHub Discussions](https://github.com/yourusername/kwhale/discussions)

---

Built with ❤️ for music lovers who value privacy and control over their data.
