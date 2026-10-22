-- P8.1 reliability upgrade for databases where 052 was already applied.
ALTER TABLE seasons ADD COLUMN IF NOT EXISTS season_seed BIGINT;
ALTER TABLE seasons ADD COLUMN IF NOT EXISTS offseason_at TIMESTAMPTZ;
ALTER TABLE seasons ADD COLUMN IF NOT EXISTS preseason_at TIMESTAMPTZ;
UPDATE seasons SET season_seed=floor(random()*1000000000)::BIGINT WHERE season_seed IS NULL;
ALTER TABLE seasons ALTER COLUMN season_seed SET NOT NULL;
ALTER TABLE seasons DROP CONSTRAINT IF EXISTS seasons_state_check;
ALTER TABLE seasons ADD CONSTRAINT seasons_state_check CHECK(state IN ('PRESEASON','ACTIVE','SEASON_END','OFFSEASON','CONTRACT_WINDOW'));
ALTER TABLE player_season_state ADD COLUMN IF NOT EXISTS reward_claimed BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE season_events ADD COLUMN IF NOT EXISTS attempts INT NOT NULL DEFAULT 0;
ALTER TABLE season_events ADD COLUMN IF NOT EXISTS last_error TEXT;
ALTER TABLE season_events ADD COLUMN IF NOT EXISTS idempotency_key TEXT;
ALTER TABLE season_events ADD COLUMN IF NOT EXISTS processing_at TIMESTAMPTZ;
UPDATE season_events SET idempotency_key=md5(season_id::text || ':' || event_type || ':' || scheduled_at::text || ':' || payload::text) WHERE idempotency_key IS NULL;
ALTER TABLE season_events ALTER COLUMN idempotency_key SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_season_events_idempotency_key ON season_events(idempotency_key);
CREATE INDEX IF NOT EXISTS idx_season_events_processing ON season_events(status, processing_at, id);
ALTER TABLE season_events DROP CONSTRAINT IF EXISTS season_events_status_check;
ALTER TABLE season_events ADD CONSTRAINT season_events_status_check CHECK(status IN ('PENDING','PROCESSING','DONE','FAILED','CANCELLED'));
CREATE INDEX IF NOT EXISTS idx_season_events_failed ON season_events(status,id);

ALTER TABLE leagues DROP CONSTRAINT IF EXISTS leagues_season_state_check;
ALTER TABLE leagues ADD CONSTRAINT leagues_season_state_check CHECK(season_state IN ('PRESEASON','ACTIVE','SEASON_END','OFFSEASON','CONTRACT_WINDOW'));
