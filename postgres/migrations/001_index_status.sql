-- Migration 001: track indexing status columns
-- Adds index_status / index_error / index_attempts to track_features so the
-- worker can persist a marker for tracks whose audio analysis failed, instead
-- of re-queuing them on every index_all_tracks pass (infinite retry loop fix).
--
-- Safe to run repeatedly: all statements are IF NOT EXISTS / idempotent.

ALTER TABLE track_features
    ADD COLUMN IF NOT EXISTS index_status   TEXT DEFAULT 'ok',
    ADD COLUMN IF NOT EXISTS index_error    TEXT,
    ADD COLUMN IF NOT EXISTS index_attempts INT DEFAULT 0;

-- Backfill: existing rows with a features_vector are 'ok'; rows without one
-- (if any were created by older code) start at 'failed' so they get retried
-- a few times under the new budget rather than being treated as complete.
UPDATE track_features
   SET index_status = 'ok'
 WHERE index_status IS NULL
   AND features_vector IS NOT NULL;

UPDATE track_features
   SET index_status = 'failed'
 WHERE index_status IS NULL
   AND features_vector IS NULL;

-- Speeds up the index_all_tracks lookup that filters by status.
CREATE INDEX IF NOT EXISTS track_features_index_status
    ON track_features (index_status);
