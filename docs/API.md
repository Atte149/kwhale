# KWhale API Reference

Base URL: `http://your-server:19000/api`  
Authentication: `Authorization: Bearer <token>` (except `/auth/login`)  
OpenAPI UI: `http://your-server:19000/docs`

## Authentication

### POST /auth/login
```json
// Request
{ "username": "admin", "password": "your_password" }

// Response
{ "token": "eyJ...", "username": "admin" }
```
Token is valid for 48 hours. All other endpoints require it in the Authorization header.

---

## Library

### GET /library/search?q=&limit=
Search the local library by title, artist, or album.

**Params:** `q` (required), `limit` (default 30, max 100)

**Response:**
```json
{
  "songs": [
    {
      "id": "abc123",
      "title": "Creep",
      "artist": "Radiohead",
      "album": "Pablo Honey",
      "duration": 238,
      "coverUrl": "/api/library/cover/abc123",
      "streamUrl": "/api/stream/abc123",
      "vibe": {
        "bpm": 92.5,
        "energy": 0.71,
        "valence": 0.34,
        "tags": ["melancholic", "grunge", "introspective"]
      }
    }
  ]
}
```

### GET /library/albums?size=&offset=
List albums. `size` default 50, max 500.

### GET /library/albums/{album_id}
Get album with its tracks.

### GET /library/artists
List all artists.

### GET /library/artists/{artist_id}
Get artist with albums.

### GET /library/songs/{song_id}
Get full song details including vibe data and lyrics.

### GET /library/cover/{cover_id}?size=
Cover art — 302-redirect to Navidrome.

### POST /library/songs/{song_id}/star
Star (favorite) a track.

### DELETE /library/songs/{song_id}/star
Unstar a track.

---

## Streaming

### GET /stream/{song_id}?max_bitrate=
Stream a track — **302-redirect** to Navidrome stream URL.  
The client should follow the redirect; audio bytes come directly from Navidrome.

**Params:** `max_bitrate` (kbps, 0 = original quality, default 0)

> **Note for client developers:** Follow the redirect. Do NOT stream through the API server.
> Use ExoPlayer/just_audio with `setUrl(streamUrl)` and let it handle the redirect.

---

## Telemetry

### POST /events
Batch-ingest playback events from the client.

**Body:**
```json
{
  "events": [
    {
      "navidrome_id": "abc123",
      "event_type": "complete",
      "position_sec": 238.0,
      "duration_sec": 238.0,
      "completion_pct": 1.0,
      "skipped": false,
      "seek_count": 0,
      "source": "local",
      "context": {}
    }
  ]
}
```

**Event types:**
| type | when to send |
|---|---|
| `play` | Playback starts (after buffering) |
| `pause` | User pauses |
| `complete` | Track plays to end (≥85% completion) |
| `skip` | User skips before 85% |
| `seek` | User seeks to a new position |
| `heartbeat` | Every 30s while playing (for duration tracking) |

**Remote track telemetry:**
For tracks from remote sources, set:
```json
{
  "source": "remote:icm",
  "context": { "provider_id": "icm-track-id-123" }
}
```
After `stream_auto_acquire_threshold` plays, the track is auto-downloaded.

### POST /events/single
Single event (same body without the `events` array wrapper).

---

## Recommendations

### GET /recs?algorithm=&limit=
Get personalised recommendations.

**Params:** `algorithm` (`hybrid` | `als` | `content`), `limit` (default 20)

**Response:**
```json
{
  "tracks": [
    {
      "id": "abc123",
      "title": "...",
      "artist": "...",
      "streamUrl": "/api/stream/abc123",
      "coverUrl": "/api/library/cover/abc123",
      "rec_score": 0.87
    }
  ],
  "generated_at": "2026-05-29T14:00:00Z",
  "algorithm": "hybrid"
}
```

### POST /recs/generate?algorithm=
Trigger recommendation generation (runs as background task).

**Response:** `{ "task_id": "...", "status": "queued" }`

### GET /recs/taste-profile
Get user taste profile (avg BPM, energy, valence, skip rate, preferred hours).

---

## Discovery & Acquisition

### GET /discover?q=&limit=
Search all enabled source plugins (ICM, Yandex, Deezer, ...) for a track.

**Response:**
```json
{
  "query": "The Cure - Lovesong",
  "results": [
    {
      "provider": "icm",
      "provider_id": "12345",
      "title": "Lovesong",
      "artist": "The Cure",
      "album": "Disintegration",
      "duration_sec": 321,
      "cover_url": "https://..."
    }
  ]
}
```

### GET /discover/{provider}?q=&limit=
Search a specific provider.

### POST /discover/acquire
Queue a track download.

**Body:**
```json
{
  "query": "The Cure - Lovesong",
  "provider": "icm",
  "provider_id": "12345"
}
```
Provide either `query` (auto-selects provider) or `provider`+`provider_id` (specific).

**Response:** `{ "task_id": "...", "status": "queued" }`

### GET /discover/acquire/{task_id}
Check download status.

**Response:**
```json
{
  "id": "...",
  "status": "tagging",
  "progress_pct": 80,
  "navidrome_id": null,
  "error": null,
  "created_at": "..."
}
```

**Status values:** `pending` → `running` → `tagging` → `done` | `failed` | `cancelled`

### GET /discover/queue?limit=
List recent download tasks.

---

## Vibe (Audio Features)

### GET /vibe/{song_id}
Get Essentia audio features and vibe tags for a track.

**Response:**
```json
{
  "indexed": true,
  "navidrome_id": "abc123",
  "bpm": 92.5,
  "energy": 0.71,
  "valence": 0.34,
  "instrumentalness": 0.12,
  "danceability": 0.45,
  "loudness": 0.62,
  "key": 9,
  "mode": 0,
  "lyrics": "When you were here before...",
  "vibe_tags": ["melancholic", "grunge", "introspective", "rainy", "guitar-driven"],
  "indexed_at": "2026-05-29T10:00:00Z"
}
```

### GET /vibe/{song_id}/similar?limit=
Find similar tracks by audio feature cosine similarity.

**Response:**
```json
{
  "similar": [
    { "navidrome_id": "def456", "score": 0.94 }
  ]
}
```

### POST /vibe/{song_id}/index
Trigger indexing for a specific track.

### POST /vibe/index-all
Index all unindexed tracks (runs in background).

---

## MCP Server (for AI agents)

**Endpoint:** `http://your-server:8090/mcp` (localhost-only by default)  
**Protocol:** MCP streamable-HTTP (JSON-RPC 2.0)  
**Tools:**

| Tool | Description |
|---|---|
| `search_library` | Search local library |
| `get_similar_tracks` | Find similar tracks by audio features |
| `search_sources` | Search remote streaming sources |
| `acquire_track` | Download a track |
| `get_download_status` | Check download status |
| `get_taste_profile` | Get user taste/preference profile |
| `get_listening_stats` | Listening statistics for last N days |
| `get_recommendations` | Get personalised recommendations |
| `create_mood_playlist` | Create a playlist from a mood description |
| `trigger_indexing` | Trigger Essentia feature extraction |
| `list_providers` | List enabled source plugins |

**Connect:** Add to Claude Desktop `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "kwhale": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/sse-client",
                "http://localhost:8090/mcp"]
    }
  }
}
```
Or for direct container access: `http://your-server:8090/mcp`

**Verify with MCP Inspector:**
```bash
npx @modelcontextprotocol/inspector http://localhost:8090/mcp
```

---

## Error Format

All errors return standard HTTP status codes with a JSON body:
```json
{ "detail": "Error message here" }
```

Common codes: `401` unauthorized, `404` not found, `422` validation error, `502` upstream (Navidrome) error.

---

## Client Integration Notes (for Navic fork)

1. **Auth:** POST `/auth/login` → store token in secure storage → add as `Authorization: Bearer <token>` header.
2. **Stream:** Use `streamUrl` from search/song responses. Pass it to `just_audio` as a URI — it follows the 302 automatically.
3. **Telemetry:** Subscribe to `player.positionStream` + `player.playerStateStream`. Batch events every 30s or on state change. POST to `/events`.
4. **BT/AVRCP:** Set `MediaItem` fields from song response. Add `vibe_tags` to `extras` map for custom display on car/headphone screens that support it.
5. **Widgets:** Update widget data on `playerStateStream` changes using `home_widget` package.
6. **Discover:** Show a "Discover" tab that calls `GET /discover?q=...` and lets user tap "Download" (calls `POST /discover/acquire`).
7. **Recommendations:** Show a "For You" feed from `GET /recs`.
