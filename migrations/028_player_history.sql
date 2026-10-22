CREATE TABLE IF NOT EXISTS player_history (
    id BIGSERIAL PRIMARY KEY,
    player_id BIGINT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ovr INT NOT NULL CHECK(ovr BETWEEN 1 AND 100),
    potential INT NOT NULL CHECK(potential BETWEEN 1 AND 100),
    market_value BIGINT NOT NULL CHECK(market_value>=0),
    morale INT NOT NULL CHECK(morale BETWEEN 0 AND 100),
    fitness INT NOT NULL CHECK(fitness BETWEEN 0 AND 100),
    note TEXT
);
CREATE INDEX IF NOT EXISTS idx_player_history_player_time ON player_history(player_id,recorded_at DESC,id DESC);
