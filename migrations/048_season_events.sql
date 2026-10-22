CREATE TABLE IF NOT EXISTS season_events(
    id BIGSERIAL PRIMARY KEY,
    season_id BIGINT NOT NULL,
    event_type TEXT NOT NULL,
    scheduled_at TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','PROCESSING','DONE','FAILED','CANCELLED')),
    attempts INT NOT NULL DEFAULT 0,
    last_error TEXT,
    idempotency_key TEXT NOT NULL,
    processing_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_season_events_idempotency_key
    ON season_events(idempotency_key);
CREATE INDEX IF NOT EXISTS idx_season_events_processing
    ON season_events(status, processing_at, id);
CREATE INDEX IF NOT EXISTS idx_season_events_due
    ON season_events(status, scheduled_at, id);
CREATE INDEX IF NOT EXISTS idx_season_events_season
    ON season_events(season_id, scheduled_at, id);
