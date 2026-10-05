ALTER TABLE matches ADD COLUMN IF NOT EXISTS motm_player_id BIGINT REFERENCES players(id);
CREATE TABLE IF NOT EXISTS match_player_stats(
 id BIGSERIAL PRIMARY KEY,
 match_id BIGINT NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
 club_id BIGINT NOT NULL REFERENCES clubs(id) ON DELETE CASCADE,
 player_id BIGINT NOT NULL REFERENCES players(id),
 rating NUMERIC(3,1) NOT NULL CHECK(rating BETWEEN 0 AND 10),
 minutes INT NOT NULL DEFAULT 0 CHECK(minutes>=0),
 goals INT NOT NULL DEFAULT 0 CHECK(goals>=0),
 assists INT NOT NULL DEFAULT 0 CHECK(assists>=0),
 UNIQUE(match_id,player_id)
);
CREATE INDEX IF NOT EXISTS idx_match_player_stats_club ON match_player_stats(match_id,club_id);
