#!/usr/bin/env bash
# KWhale first-run setup script
# Run once: bash setup.sh
set -e

DATA_ROOT="${DATA_ROOT:-./data}"
MUSIC_SRC="${1:-}"

echo "=== KWhale Setup ==="
echo "Data root: $DATA_ROOT"

# Create directory structure
mkdir -p "$DATA_ROOT/music/library"
mkdir -p "$DATA_ROOT/music/incoming"
mkdir -p "$DATA_ROOT/music/failed"
mkdir -p "$DATA_ROOT/navidrome"
mkdir -p "$DATA_ROOT/postgres"
mkdir -p "$DATA_ROOT/redis"

echo "[+] Created data directories"

# If existing music library is provided, symlink it
if [ -n "$MUSIC_SRC" ] && [ -d "$MUSIC_SRC" ]; then
    echo "[+] Symlinking existing library from $MUSIC_SRC → $DATA_ROOT/music/library"
    # Note: symlink won't work across Docker mounts — copy or move instead
    # For a symlink to work, the source must be accessible inside the container
    echo "    NOTE: For Docker to see the library, $MUSIC_SRC must be mounted."
    echo "    Easiest: move files into $DATA_ROOT/music/library/"
    echo "    To move: mv $MUSIC_SRC/* $DATA_ROOT/music/library/"
fi

# Copy env files if they don't exist
if [ ! -f .env ]; then
    cp .env.example .env
    echo "[+] Created .env — EDIT IT before starting"
fi
if [ ! -f api/.env ]; then
    cp api/.env.example api/.env
    echo "[+] Created api/.env — EDIT IT before starting"
fi
if [ ! -f worker/.env ]; then
    cp worker/.env.example worker/.env
    echo "[+] Created worker/.env — EDIT IT before starting"
fi
if [ ! -f mcp/.env ]; then
    echo "DATABASE_URL=postgresql://kwhale:your_password@postgres:5432/kwhale" > mcp/.env
    echo "[+] Created mcp/.env — UPDATE the password to match .env"
fi

echo ""
echo "=== Next steps ==="
echo "1. Edit .env and set DATA_ROOT, passwords, and credentials"
echo "2. Edit api/.env and worker/.env with your API keys"
echo "3. docker compose build"
echo "4. docker compose up -d"
echo "5. Open http://localhost:19000/docs to verify"
echo "6. Create Navidrome admin at http://localhost:4535 (first launch)"
echo "7. POST http://localhost:19000/api/auth/login to get your token"
echo "8. POST http://localhost:19000/api/vibe/index-all to start indexing"
