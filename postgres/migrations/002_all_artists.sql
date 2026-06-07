-- Migration 002: collaborative artist tagging
-- Adds all_artists (text[]) and artists_indexed_at to track_features so the
-- recommender, library search, and similarity logic can credit every
-- performer on a track, not just the primary `artist`. VorBis `artists` is
-- the source of truth (multi-value, ';' separated); we fall back to
-- `artist` and finally to parsing "(feat. X & Y)" out of the title.
--
-- Safe to run repeatedly: all statements are IF NOT EXISTS / idempotent.

ALTER TABLE track_features
    ADD COLUMN IF NOT EXISTS all_artists        text[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS artists_indexed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS all_artists_text   text   NOT NULL DEFAULT '';

-- Trigram GIN does not work on text[] (gin_trgm_ops has no array overload).
-- We mirror the array into all_artists_text (maintained by the indexer
-- alongside all_artists) and index that with the trigram opclass. The
-- query then becomes `all_artists_text ILIKE '%name%'` and the planner
-- uses this index just like the existing artist / title trigram indexes.
CREATE INDEX IF NOT EXISTS track_features_all_artists_text_trgm
    ON track_features USING gin (all_artists_text gin_trgm_ops);

-- artists_indexed_at is null until the indexer has populated all_artists.
-- index_all_tracks uses this to drain the backfill cheaply (no Essentia).
