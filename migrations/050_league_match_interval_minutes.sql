ALTER TABLE leagues
    ADD COLUMN IF NOT EXISTS match_interval_minutes INT NULL;

ALTER TABLE leagues
    DROP CONSTRAINT IF EXISTS leagues_match_interval_minutes_positive;

ALTER TABLE leagues
    ADD CONSTRAINT leagues_match_interval_minutes_positive
    CHECK (match_interval_minutes IS NULL OR match_interval_minutes > 0);
