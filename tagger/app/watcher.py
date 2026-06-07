"""File system watcher — polls incoming/ and dispatches to tagger."""
import os
import time
from pathlib import Path

import httpx

INCOMING_DIR = Path(os.environ.get("INCOMING_DIR", "/data/incoming"))
TAGGER_URL = os.environ.get("TAGGER_URL", "http://tagger:8093")
POLL_INTERVAL = int(os.environ.get("WATCHER_POLL_INTERVAL", "15"))
SETTLE_SECONDS = int(os.environ.get("WATCHER_SETTLE_SECONDS", "10"))

AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".ogg", ".opus", ".wav", ".aac"}
_dispatched: set[str] = set()


def _is_settled(path: Path) -> bool:
    try:
        mtime = path.stat().st_mtime
        return (time.time() - mtime) >= SETTLE_SECONDS
    except FileNotFoundError:
        return False


def run():
    print(f"Watcher: polling {INCOMING_DIR} every {POLL_INTERVAL}s")
    INCOMING_DIR.mkdir(parents=True, exist_ok=True)
    client = httpx.Client(timeout=10.0)

    while True:
        for fp in INCOMING_DIR.rglob("*"):
            # Skip the failed/ quarantine dir -- re-dispatching those files just
            # fails again (SameFileError) and loops forever across restarts.
            if "failed" in fp.relative_to(INCOMING_DIR).parts:
                continue
            if (
                fp.is_file()
                and fp.suffix.lower() in AUDIO_EXTS
                and str(fp) not in _dispatched
                and _is_settled(fp)
            ):
                try:
                    client.post(f"{TAGGER_URL}/tag", json={"filepath": str(fp)})
                    _dispatched.add(str(fp))
                    print(f"Dispatched: {fp.name}")
                except Exception as e:
                    print(f"Dispatch error: {e}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()
