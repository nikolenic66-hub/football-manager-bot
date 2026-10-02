ALTER TABLE matches ADD COLUMN IF NOT EXISTS home_possession NUMERIC(5,2) NOT NULL DEFAULT 50;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS away_possession NUMERIC(5,2) NOT NULL DEFAULT 50;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS home_shots INT NOT NULL DEFAULT 0;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS away_shots INT NOT NULL DEFAULT 0;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS home_shots_on_target INT NOT NULL DEFAULT 0;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS away_shots_on_target INT NOT NULL DEFAULT 0;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS home_corners INT NOT NULL DEFAULT 0;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS away_corners INT NOT NULL DEFAULT 0;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS home_subs_used INT NOT NULL DEFAULT 0;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS away_subs_used INT NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS club_substitution_plans(
 id BIGSERIAL PRIMARY KEY,
 club_id BIGINT NOT NULL REFERENCES clubs(id) ON DELETE CASCADE,
 minute INT NOT NULL CHECK(minute BETWEEN 46 AND 90),
 player_off_id BIGINT NOT NULL REFERENCES players(id),
 player_on_id BIGINT NOT NULL REFERENCES players(id),
 enabled BOOLEAN NOT NULL DEFAULT TRUE,
 UNIQUE(club_id,minute,player_off_id)
);

CREATE TABLE IF NOT EXISTS match_substitutions(
 id BIGSERIAL PRIMARY KEY,
 match_id BIGINT NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
 club_id BIGINT NOT NULL REFERENCES clubs(id),
 minute INT NOT NULL,
 player_off_id BIGINT NOT NULL REFERENCES players(id),
 player_on_id BIGINT NOT NULL REFERENCES players(id),
 UNIQUE(match_id,club_id,minute,player_off_id)
);

CREATE INDEX IF NOT EXISTS idx_sub_plans_club ON club_substitution_plans(club_id,enabled,minute);
CREATE INDEX IF NOT EXISTS idx_match_subs_match ON match_substitutions(match_id,minute);
ALTER TABLE club_players ADD COLUMN IF NOT EXISTS suspension_matches INT NOT NULL DEFAULT 0 CHECK(suspension_matches>=0);
