CREATE TABLE IF NOT EXISTS league_history (
    id BIGSERIAL PRIMARY KEY,
    league_id BIGINT NOT NULL,
    league_name TEXT NOT NULL,
    season_number INT NOT NULL DEFAULT 1,
    club_id BIGINT NOT NULL,
    club_name TEXT NOT NULL,
    place INT NOT NULL,
    played INT NOT NULL DEFAULT 0,
    wins INT NOT NULL DEFAULT 0,
    draws INT NOT NULL DEFAULT 0,
    losses INT NOT NULL DEFAULT 0,
    goals_for INT NOT NULL DEFAULT 0,
    goals_against INT NOT NULL DEFAULT 0,
    points INT NOT NULL DEFAULT 0,
    archived_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_league_history_league ON league_history(league_id);
CREATE INDEX IF NOT EXISTS idx_league_history_club ON league_history(club_id);
