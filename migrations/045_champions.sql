CREATE TABLE IF NOT EXISTS champions_league(
    id BIGSERIAL PRIMARY KEY,
    season INT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('WAITING','ACTIVE','FINISHED'))
);

CREATE TABLE IF NOT EXISTS champions_groups(
    id BIGSERIAL PRIMARY KEY,
    cl_id BIGINT NOT NULL REFERENCES champions_league(id) ON DELETE CASCADE,
    group_name TEXT NOT NULL,
    UNIQUE(cl_id,group_name)
);

CREATE TABLE IF NOT EXISTS champions_matches(
    id BIGSERIAL PRIMARY KEY,
    cl_id BIGINT NOT NULL REFERENCES champions_league(id) ON DELETE CASCADE,
    group_name TEXT NOT NULL,
    home_club_id BIGINT NOT NULL REFERENCES clubs(id),
    away_club_id BIGINT NOT NULL REFERENCES clubs(id),
    home_score INT,
    away_score INT,
    winner_id BIGINT REFERENCES clubs(id),
    status TEXT NOT NULL DEFAULT 'SCHEDULED' CHECK(status IN ('SCHEDULED','LIVE','FINISHED')),
    match_id BIGINT REFERENCES matches(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_champions_matches_cl_group ON champions_matches(cl_id,group_name,id);
CREATE INDEX IF NOT EXISTS idx_champions_status ON champions_league(status,season);
