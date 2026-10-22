-- Stable seven-tier league pyramid and season-scoped fixture history.
-- Safe to run repeatedly; valid existing tier values are preserved.
ALTER TABLE leagues ADD COLUMN IF NOT EXISTS tier VARCHAR(20) NOT NULL DEFAULT 'ROOKIE';

UPDATE leagues
SET tier='ROOKIE'
WHERE tier IS NULL OR tier NOT IN ('ROOKIE','BRONZE','SILVER','GOLD','DIAMOND','ELITE','LEGEND');

ALTER TABLE leagues ALTER COLUMN tier SET DEFAULT 'ROOKIE';
ALTER TABLE leagues ALTER COLUMN tier SET NOT NULL;
ALTER TABLE leagues DROP CONSTRAINT IF EXISTS leagues_tier_check;
ALTER TABLE leagues ADD CONSTRAINT leagues_tier_check
    CHECK (tier IN ('ROOKIE','BRONZE','SILVER','GOLD','DIAMOND','ELITE','LEGEND'));
CREATE INDEX IF NOT EXISTS idx_leagues_tier ON leagues(tier);

-- The previous global fixture index prevented a club pair from meeting again
-- in the same league in a later season. Keep uniqueness per season instead.
DROP INDEX IF EXISTS uq_match_fixture;
CREATE UNIQUE INDEX IF NOT EXISTS uq_match_fixture_season
    ON matches(season_id, league_id, round, home_club_id, away_club_id);
CREATE INDEX IF NOT EXISTS idx_matches_season_league
    ON matches(season_id, league_id, scheduled_at);
