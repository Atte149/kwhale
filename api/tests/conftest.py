"""Pytest config for the API test suite.

Ensures `app` is importable as a package and that Settings() can instantiate
without a populated .env (config reads os.environ, so we seed minimal values).
"""
import os
import sys
from pathlib import Path

# Make the api/ directory importable so `import app.xxx` resolves.
_API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_API_ROOT))

# Seed the few settings the modules read at import time. Values are dummies —
# no test here touches the network or DB.
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("OPENAI_API_KEY", "")
os.environ.setdefault("JWT_SECRET", "test-secret")
