CREATE TABLE IF NOT EXISTS season_pass_rewards(
    id BIGSERIAL PRIMARY KEY,
    season_pass_id BIGINT NOT NULL REFERENCES season_pass(id) ON DELETE CASCADE,
    level INT NOT NULL CHECK(level>=1),
    reward_type TEXT NOT NULL,
    reward_value JSONB NOT NULL,
    UNIQUE(season_pass_id,level)
);
CREATE INDEX IF NOT EXISTS idx_season_pass_rewards_season ON season_pass_rewards(season_pass_id,level);
