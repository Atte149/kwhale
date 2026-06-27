# Contributing to KWhale

Thank you for your interest in contributing to KWhale! This document covers
the basics of getting started.

## Development Setup

```bash
# Clone
git clone https://github.com/Atte149/kwhale.git
cd kwhale

# Set up environment files (see .env.example)
cp .env.example .env       # edit with your passwords/API keys
cp api/.env.example api/.env
cp worker/.env.example worker/.env
cp mcp/.env.example mcp/.env

# Create data directories
mkdir -p kwhale-data/{music/library,music/incoming,navidrome,postgres,redis}

# Build and start
docker compose build
docker compose up -d

# Verify
curl http://localhost:19000/healthz
```

## Project Structure

```
kwhale/
├── api/          FastAPI — REST API + Subsonic proxy
├── worker/       Celery — audio analysis, recommendations, source plugins
├── tagger/       Auto-tagger — Shazam/AcoustID metadata resolution
├── embedding/    bge-m3 embedding service
├── mcp/          MCP server for AI agent integration
├── postgres/     DB schema + migrations
├── docs/         Documentation
├── scripts/      Setup + smoke test scripts
├── install.py    One-command installer
├── kwhalectl.py  Management CLI
└── docker-compose.yml
```

## Making Changes

1. **Fork** the repository and create a branch:
   ```bash
   git checkout -b feat/my-feature
   ```

2. **Make your changes.** Follow the existing code style:
   - Python: type hints, docstrings, `from __future__ import annotations`
   - Keep functions focused — one responsibility per module
   - No secrets in code — use env vars

3. **Test your changes:**
   ```bash
   # Python tests
   cd api && python -m pytest tests/
   cd worker && python -m pytest tests/

   # Lint (if you have ruff)
   ruff check api/ worker/ tagger/

   # End-to-end smoke test
   python3 kwhalectl.py smoke
   ```

4. **Rebuild affected services:**
   ```bash
   docker compose build api worker && docker compose up -d --force-recreate api worker worker-beat
   ```

5. **Commit** with a clear message:
   ```bash
   git commit -m "feat: add X"     # new feature
   git commit -m "fix: resolve Y"  # bug fix
   git commit -m "docs: update Z"  # documentation
   ```

6. **Open a Pull Request** with a description of what changed and why.

## Adding a Source Plugin

Source plugins are auto-discovered. To add one:

1. Create `worker/app/providers/my_source.py`
2. Subclass `BaseProvider` and implement `search()`, `resolve()`, `download()`
3. Set `name`, `priority` (lower = preferred), and `is_available()` (return
   `False` without credentials)
4. Rebuild the worker: `docker compose build worker && docker compose up -d --force-recreate worker`

## Reporting Issues

- **Bugs:** Use [GitHub Issues](https://github.com/Atte149/kwhale/issues) with
  the bug report template. Include logs (`docker compose logs api worker`) and
  steps to reproduce.
- **Feature requests:** Use GitHub Discussions or the feature request template.
- **Security issues:** See [SECURITY.md](SECURITY.md) — do NOT open a public
  issue for security vulnerabilities.

## Code of Conduct

See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Be respectful and constructive.