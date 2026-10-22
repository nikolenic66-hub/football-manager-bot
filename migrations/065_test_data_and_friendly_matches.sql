-- Explicitly tag only test fixtures; existing real clubs/matches remain non-test.
ALTER TABLE clubs ADD COLUMN IF NOT EXISTS is_test BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE leagues ADD COLUMN IF NOT EXISTS is_test BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS is_test BOOLEAN NOT NULL DEFAULT FALSE;

-- Friendly test matches do not belong to a league or season.
ALTER TABLE matches ALTER COLUMN league_id DROP NOT NULL;
ALTER TABLE matches DROP CONSTRAINT IF EXISTS matches_competition_type_check;
ALTER TABLE matches ADD CONSTRAINT matches_competition_type_check
    CHECK (competition_type IN ('LEAGUE','CUP','CHAMPIONS','FRIENDLY'));

CREATE INDEX IF NOT EXISTS idx_clubs_is_test ON clubs(is_test) WHERE is_test=TRUE;
CREATE INDEX IF NOT EXISTS idx_leagues_is_test ON leagues(is_test) WHERE is_test=TRUE;
CREATE INDEX IF NOT EXISTS idx_matches_is_test ON matches(is_test) WHERE is_test=TRUE;
CREATE INDEX IF NOT EXISTS idx_matches_friendly_test ON matches(competition_type,status,id)
    WHERE is_test=TRUE AND competition_type='FRIENDLY';

-- Safe backfill for legacy admin-created test clubs and the explicitly named quick-test league.
UPDATE clubs c SET is_test=TRUE
WHERE EXISTS (
    SELECT 1 FROM admin_actions_log a
    WHERE a.action='test_bot_club_create' AND a.target_type='club' AND a.target_id=c.id
);
UPDATE leagues SET is_test=TRUE WHERE name ILIKE 'FM27 Quick Test%';
UPDATE clubs c SET is_test=TRUE
WHERE c.owner_user_id IN (SELECT u.id FROM users u WHERE u.telegram_id<0)
  AND EXISTS (SELECT 1 FROM league_teams lt JOIN leagues l ON l.id=lt.league_id
              WHERE lt.club_id=c.id AND l.is_test=TRUE);
