#!/bin/bash
# Run full library retag from host (with write access)
# Usage: sudo bash run_retag.sh [--limit N] [--force]

cd /files/kwhale/server/worker

export LIBRARY_DIR=/files/kwhale/data/music/library
export DATA_DIR=/files/kwhale/data
export DATABASE_URL="postgresql://kwhale:kwhale_secret@192.168.1.119:5434/kwhale"
export YANDEX_MUSIC_TOKEN=$(grep YANDEX_MUSIC_TOKEN /files/kwhale/server/api/.env | cut -d= -f2)
export TAGGER_URL="http://127.0.0.1:8093"
export NAVIDROME_DB="/files/kwhale/data/navidrome/navidrome.db"

python3 -c "
import sys, os, json
sys.path.insert(0, '.')
from app.retagger import retag_track, scan_library, read_tags
from pathlib import Path

LIBRARY = Path(os.environ['LIBRARY_DIR'])
AUDIO_EXTS = {'.mp3', '.flac', '.m4a', '.ogg', '.opus', '.wav', '.aac'}

limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
force = '--force' in sys.argv or True  # always force for full retag

files = sorted(f for f in LIBRARY.rglob('*') if f.suffix.lower() in AUDIO_EXTS)
if limit:
    files = files[:limit]

stats = {'total': len(files), 'retagged': 0, 'skipped': 0, 'failed': 0}
for i, f in enumerate(files):
    fpath = str(f)
    try:
        result = retag_track(fpath, force=force)
        action = result['action']
        stats[action] = stats.get(action, 0) + 1
        if action == 'retagged':
            old_a = result['old_tags'].get('artist', '?')
            new_a = result.get('new_tags', {}).get('artist', '?')
            if old_a != new_a:
                print(f'  OK: {f.name}: {old_a} -> {new_a}')
        elif action == 'failed':
            print(f'  FAIL: {f.name}: {result.get(\"source\",\"?\")}')
    except Exception as e:
        print(f'  ERROR: {f.name}: {e}')
        stats['failed'] += 1
    if (i+1) % 50 == 0:
        print(f'Progress: {i+1}/{stats[\"total\"]} (retagged={stats[\"retagged\"]}, skipped={stats[\"skipped\"]}, failed={stats[\"failed\"]})', flush=True)

print(f'\\n{\"=\"*40}')
print(f'COMPLETE')
for k, v in stats.items():
    print(f'  {k}: {v}')
" "$@"