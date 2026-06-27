-- KWhale Database Schema
-- PostgreSQL 16 + pgvector

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ── Track audio features + embeddings ────────────────────────────────────────
-- navidrome_id = Navidrome's internal track ID (stable, filesystem-based hash)
CREATE TABLE IF NOT EXISTS track_features (
    id              SERIAL PRIMARY KEY,
    navidrome_id    TEXT UNIQUE NOT NULL,
    filepath        TEXT NOT NULL,
    title           TEXT,
    artist          TEXT,
    album           TEXT,
    duration_sec    FLOAT,

    -- Essentia audio features
    bpm             FLOAT,
    energy          FLOAT,
    valence         FLOAT,
    instrumentalness FLOAT,
    danceability    FLOAT,
    loudness        FLOAT,
    key             INT,
    mode            INT,         -- 0=minor, 1=major

    -- 20-dimensional audio feature vector for similarity search
    features_vector vector(20),

    -- Lyrics + semantic embedding (bge-m3, 1024-dim)
    lyrics          TEXT,
    lyrics_embedding vector(1024),

    -- LLM-generated descriptive tags e.g. ["melancholic","driving","late-night"]
    vibe_tags       JSONB DEFAULT '[]',

    -- Spectrogram analysis by multimodal model (mimo-v2-omni)
    spectro_desc    TEXT,
    spectro_tags    JSONB DEFAULT '[]',

    -- Indexing status: lets us persist a row for tracks whose audio analysis
    -- failed so they are not retried forever (see worker indexer.index_track).
    index_status    TEXT DEFAULT 'ok',   -- 'ok' | 'failed'
    index_error     TEXT,
    index_attempts  INT DEFAULT 0,

    -- All credited artists on the track (primary + featured collaborators).
    -- Sourced from the VorBis `artists` tag (multi-value, ';' separated),
    -- falling back to `artist` and finally to a title "(feat. X & Y)" parse.
    -- Used by the recommender, library search, and track-similarity logic.
    all_artists        text[] NOT NULL DEFAULT '{}',
    all_artists_text   text   NOT NULL DEFAULT '',  -- mirror of all_artists for trigram GIN
    artists_indexed_at TIMESTAMPTZ,

    indexed_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS track_features_audio_hnsw
    ON track_features USING hnsw (features_vector vector_cosine_ops);

CREATE INDEX IF NOT EXISTS track_features_lyrics_hnsw
    ON track_features USING hnsw (lyrics_embedding vector_cosine_ops)
    WHERE lyrics_embedding IS NOT NULL;

CREATE INDEX IF NOT EXISTS track_features_artist_trgm
    ON track_features USING gin (artist gin_trgm_ops);

CREATE INDEX IF NOT EXISTS track_features_title_trgm
    ON track_features USING gin (title gin_trgm_ops);

-- Trigram index over the multi-artist array (via the mirror text column)
-- so the recommender can ask "any track featuring $NAME" in O(log n)
-- instead of scanning the table. GIN trigram does not work on text[]
-- directly, hence the mirror.
CREATE INDEX IF NOT EXISTS track_features_all_artists_text_trgm
    ON track_features USING gin (all_artists_text gin_trgm_ops);

-- ── Playback events (rich telemetry from the client) ─────────────────────────
CREATE TABLE IF NOT EXISTS playback_events (
    id              BIGSERIAL PRIMARY KEY,
    user_id         TEXT NOT NULL DEFAULT 'default',
    navidrome_id    TEXT NOT NULL,
    event_type      TEXT NOT NULL,  -- 'play','pause','complete','skip','seek','heartbeat'

    -- Timing
    ts              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    hour_of_day     INT GENERATED ALWAYS AS (EXTRACT(HOUR FROM ts AT TIME ZONE 'Europe/Moscow')::INT) STORED,
    day_of_week     INT GENERATED ALWAYS AS (EXTRACT(DOW FROM ts AT TIME ZONE 'Europe/Moscow')::INT) STORED,

    -- Position data (from just_audio positionStream)
    position_sec    FLOAT,
    duration_sec    FLOAT,
    completion_pct  FLOAT,         -- 0.0–1.0

    -- Context
    skipped         BOOLEAN DEFAULT FALSE,
    seek_count      INT DEFAULT 0,
    source          TEXT DEFAULT 'local',  -- 'local', 'remote:icm', 'remote:yandex'
    context         JSONB DEFAULT '{}'     -- e.g. {"playlist_id": "...", "shuffle": true}
);

CREATE INDEX IF NOT EXISTS pe_user_track ON playback_events (user_id, navidrome_id);
CREATE INDEX IF NOT EXISTS pe_ts ON playback_events (ts DESC);
CREATE INDEX IF NOT EXISTS pe_event_type ON playback_events (event_type);

-- ── Daily recommendations ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS recommendations (
    user_id         TEXT NOT NULL,
    date            DATE NOT NULL,
    algorithm       TEXT NOT NULL DEFAULT 'hybrid',  -- 'als','content','hybrid'
    track_ids       JSONB NOT NULL DEFAULT '[]',     -- ordered list of navidrome_ids
    scores          JSONB DEFAULT '{}',              -- navidrome_id → score
    generated_at    TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (user_id, date, algorithm)
);

-- ── Download queue ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS download_queue (
    id              TEXT PRIMARY KEY,        -- Celery task ID
    user_id         TEXT DEFAULT 'default',
    status          TEXT NOT NULL DEFAULT 'pending',
    -- 'pending','running','tagging','done','failed','cancelled'

    query           TEXT NOT NULL,           -- original search query or provider ID
    provider        TEXT,                    -- 'icm','yandex','deezer', etc.
    provider_id     TEXT,                    -- provider-specific track ID
    navidrome_id    TEXT,                    -- filled after tagger+scan finish

    error           TEXT,
    progress_pct    FLOAT DEFAULT 0,

    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ── Source cache (avoid re-searching providers) ───────────────────────────────
CREATE TABLE IF NOT EXISTS source_cache (
    cache_key       TEXT PRIMARY KEY,  -- sha256(provider+query)
    provider        TEXT NOT NULL,
    result_json     JSONB NOT NULL,
    expires_at      TIMESTAMPTZ NOT NULL
);

-- ── Provider→Navidrome ID mapping ─────────────────────────────────────────────
-- When a track from a remote source is downloaded, we record the mapping
-- so we can correlate /discover results with local library entries.
CREATE TABLE IF NOT EXISTS provider_track_map (
    provider        TEXT NOT NULL,
    provider_id     TEXT NOT NULL,
    navidrome_id    TEXT,
    status          TEXT DEFAULT 'pending',  -- 'pending','available','failed'
    mapped_at       TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (provider, provider_id)
);

-- ── Track blacklist ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS track_blacklist (
    id              SERIAL PRIMARY KEY,
    title_norm      TEXT NOT NULL,
    artist_norm     TEXT NOT NULL,
    reason          TEXT,
    added_at        TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (title_norm, artist_norm)
);

-- ── User taste profile (materialised periodically by worker) ──────────────────
CREATE TABLE IF NOT EXISTS taste_profile (
    user_id             TEXT PRIMARY KEY,
    avg_bpm             FLOAT,
    avg_energy          FLOAT,
    avg_valence         FLOAT,
    top_vibe_tags       JSONB DEFAULT '[]',
    completion_rate_30d FLOAT,
    skip_rate_30d       FLOAT,
    preferred_hours     JSONB DEFAULT '[]',  -- list of hour-of-day ints
    computed_at         TIMESTAMPTZ DEFAULT NOW()
);

-- ── Stream counter (for auto-download after N remote plays) ───────────────────
-- Replaces the old stream_counter hack — now fed by /events not by URL requests
CREATE TABLE IF NOT EXISTS stream_counter (
    provider        TEXT NOT NULL,
    provider_id     TEXT NOT NULL,
    user_id         TEXT NOT NULL DEFAULT 'default',
    play_count      INT DEFAULT 0,
    last_played_at  TIMESTAMPTZ,
    auto_acquired   BOOLEAN DEFAULT FALSE,
    PRIMARY KEY (provider, provider_id, user_id)
);

-- ── Tag revisions (backup of old tags before retagging) ────────────────────────
CREATE TABLE IF NOT EXISTS tag_revisions (
    id              SERIAL PRIMARY KEY,
    navidrome_id    TEXT,
    filepath        TEXT NOT NULL,
    old_tags        JSONB NOT NULL DEFAULT '{}',
    new_tags        JSONB NOT NULL DEFAULT '{}',
    source          TEXT NOT NULL DEFAULT 'retag',  -- 'shazam','acoustid','filename','manual'
    classification  TEXT NOT NULL DEFAULT 'bad',    -- 'bad','uncertain','good'
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS tag_revisions_navidrome ON tag_revisions (navidrome_id);
CREATE INDEX IF NOT EXISTS tag_revisions_created ON tag_revisions (created_at DESC);

-- ── Artist aliases (translit / alternate names) ────────────────────────────────
CREATE TABLE IF NOT EXISTS artist_aliases (
    id              SERIAL PRIMARY KEY,
    artist_name     TEXT NOT NULL,     -- canonical (e.g. "Сплин")
    alias           TEXT NOT NULL,     -- alternate  (e.g. "Splen")
    alias_type      TEXT NOT NULL DEFAULT 'translit',  -- 'translit','aka','former'
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (artist_name, alias)
);

CREATE INDEX IF NOT EXISTS artist_aliases_alias_trgm
    ON artist_aliases USING gin (alias gin_trgm_ops);
CREATE INDEX IF NOT EXISTS artist_aliases_name_trgm
    ON artist_aliases USING gin (artist_name gin_trgm_ops);
