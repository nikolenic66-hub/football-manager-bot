ALTER TABLE clubs ADD COLUMN IF NOT EXISTS debt BIGINT NOT NULL DEFAULT 0 CHECK(debt>=0);
-- Concurrency, idempotency and query-path hardening.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM clubs GROUP BY owner_user_id HAVING COUNT(*) > 1) THEN
        CREATE UNIQUE INDEX IF NOT EXISTS uq_clubs_owner_user ON clubs(owner_user_id);
    END IF;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS uq_match_fixture ON matches(league_id,round,home_club_id,away_club_id);
CREATE INDEX IF NOT EXISTS idx_matches_due ON matches(scheduled_at,id) WHERE status='SCHEDULED';
CREATE INDEX IF NOT EXISTS idx_match_events_cursor ON match_events(match_id,id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_match_finance_entry ON club_financial_transactions(match_id,club_id,category) WHERE match_id IS NOT NULL;
