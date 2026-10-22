-- FAILED matches are terminal for progression but are not normal match results.
ALTER TABLE matches DROP CONSTRAINT IF EXISTS matches_status_check;
ALTER TABLE matches ADD CONSTRAINT matches_status_check
    CHECK (status IN ('SCHEDULED','LIVE','FINISHED','FAILED'));

-- One marker per league match makes standings/economy finalization idempotent
-- and records technical failures without fabricating a score/result.
CREATE TABLE IF NOT EXISTS league_match_finalizations (
    match_id BIGINT PRIMARY KEY REFERENCES matches(id) ON DELETE CASCADE,
    league_id BIGINT NOT NULL REFERENCES leagues(id) ON DELETE CASCADE,
    round_no INT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('FINISHED','TECHNICAL_FAILED')),
    diagnostic TEXT,
    finalized_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_league_match_finalizations_league_round
    ON league_match_finalizations(league_id,round_no);
