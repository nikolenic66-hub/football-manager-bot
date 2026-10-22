CREATE TABLE IF NOT EXISTS season_pass(
    id BIGSERIAL PRIMARY KEY,
    season_id BIGINT NOT NULL UNIQUE,
    start_date TIMESTAMPTZ NOT NULL,
    end_date TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE','FINISHED','DRAFT')),
    CHECK(end_date>start_date)
);

CREATE TABLE IF NOT EXISTS season_pass_progress(
    id BIGSERIAL PRIMARY KEY,
    club_id BIGINT NOT NULL REFERENCES clubs(id) ON DELETE CASCADE,
    season_pass_id BIGINT NOT NULL REFERENCES season_pass(id) ON DELETE CASCADE,
    points INT NOT NULL DEFAULT 0 CHECK(points>=0),
    level INT NOT NULL DEFAULT 0 CHECK(level>=0),
    claimed_rewards JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE(club_id,season_pass_id)
);
CREATE INDEX IF NOT EXISTS idx_season_pass_progress_club ON season_pass_progress(club_id,season_pass_id);
CREATE INDEX IF NOT EXISTS idx_season_pass_status ON season_pass(status,start_date,end_date);
