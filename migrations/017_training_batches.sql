ALTER TABLE player_development_log
    ADD COLUMN IF NOT EXISTS batch_id TEXT,
    ADD COLUMN IF NOT EXISTS intensity TEXT;
CREATE INDEX IF NOT EXISTS idx_player_development_log_batch
    ON player_development_log(club_id,batch_id,created_at DESC);
