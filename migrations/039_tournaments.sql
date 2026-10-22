ALTER TABLE leagues
    ADD COLUMN IF NOT EXISTS tier VARCHAR(20) NOT NULL DEFAULT 'ROOKIE';

ALTER TABLE leagues
    ADD COLUMN IF NOT EXISTS match_interval_days INT NOT NULL DEFAULT 1;

ALTER TABLE leagues
    ADD COLUMN IF NOT EXISTS match_time VARCHAR(5) NOT NULL DEFAULT '21:00';

ALTER TABLE leagues
    ADD COLUMN IF NOT EXISTS start_date DATE;

ALTER TABLE matches
    ADD COLUMN IF NOT EXISTS competition_type VARCHAR(20) NOT NULL DEFAULT 'LEAGUE';

UPDATE leagues SET tier='ROOKIE';

ALTER TABLE leagues
    ADD CONSTRAINT leagues_tier_check
    CHECK (tier IN ('ROOKIE','BRONZE','SILVER','GOLD','DIAMOND','ELITE','LEGEND'));

ALTER TABLE matches
    ADD CONSTRAINT matches_competition_type_check
    CHECK (competition_type IN ('LEAGUE','CUP','CHAMPIONS'));

CREATE INDEX IF NOT EXISTS idx_leagues_tier ON leagues(tier);
CREATE INDEX IF NOT EXISTS idx_matches_competition_type ON matches(competition_type);
