-- Opponent scouting and explicit counter-plan storage.
CREATE TABLE IF NOT EXISTS club_counterplans(
 id BIGSERIAL PRIMARY KEY,
 club_id BIGINT NOT NULL REFERENCES clubs(id) ON DELETE CASCADE,
 opponent_club_id BIGINT NOT NULL REFERENCES clubs(id) ON DELETE CASCADE,
 target_player_id BIGINT REFERENCES players(id) ON DELETE SET NULL,
 focus TEXT NOT NULL DEFAULT 'BALANCED',
 intensity INT NOT NULL DEFAULT 50 CHECK(intensity BETWEEN 0 AND 100),
 UNIQUE(club_id,opponent_club_id)
);
CREATE INDEX IF NOT EXISTS idx_counterplans_club_opponent ON club_counterplans(club_id,opponent_club_id);
