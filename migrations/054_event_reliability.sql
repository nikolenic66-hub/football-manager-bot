-- P8.2 event reliability: durable idempotency and stale-worker recovery.
ALTER TABLE season_events ADD COLUMN IF NOT EXISTS idempotency_key TEXT;
ALTER TABLE season_events ADD COLUMN IF NOT EXISTS processing_at TIMESTAMPTZ;

UPDATE season_events
SET idempotency_key = md5(
    season_id::text || ':' || event_type || ':' || scheduled_at::text || ':' || payload::text
)
WHERE idempotency_key IS NULL;

ALTER TABLE season_events ALTER COLUMN idempotency_key SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_season_events_idempotency_key
    ON season_events(idempotency_key);
CREATE INDEX IF NOT EXISTS idx_season_events_processing
    ON season_events(status, processing_at, id);
