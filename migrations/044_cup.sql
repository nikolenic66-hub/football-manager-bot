CREATE TABLE IF NOT EXISTS cups(
    id BIGSERIAL PRIMARY KEY,
    league_id BIGINT NOT NULL REFERENCES leagues(id) ON DELETE CASCADE,
    season INT NOT NULL,
    status TEXT NOT NULL DEFAULT 'WAITING' CHECK(status IN ('WAITING','ACTIVE','FINISHED')),
    UNIQUE(league_id,season)
);

CREATE TABLE IF NOT EXISTS cup_matches(
    id BIGSERIAL PRIMARY KEY,
    cup_id BIGINT NOT NULL REFERENCES cups(id) ON DELETE CASCADE,
    round INT NOT NULL,
    home_club_id BIGINT NOT NULL REFERENCES clubs(id),
    away_club_id BIGINT NOT NULL REFERENCES clubs(id),
    home_score INT,
    away_score INT,
    winner_id BIGINT REFERENCES clubs(id),
    status TEXT NOT NULL DEFAULT 'SCHEDULED' CHECK(status IN ('SCHEDULED','LIVE','FINISHED')),
    match_id BIGINT REFERENCES matches(id) ON DELETE SET NULL,
    UNIQUE(cup_id,round,home_club_id,away_club_id)
);
CREATE INDEX IF NOT EXISTS idx_cups_league_status ON cups(league_id,status);
CREATE INDEX IF NOT EXISTS idx_cup_matches_cup_round ON cup_matches(cup_id,round,id);
