-- P8 persistent multi-season lifecycle.
CREATE TABLE IF NOT EXISTS seasons(
    id BIGSERIAL PRIMARY KEY,
    season_number INT NOT NULL UNIQUE,
    state VARCHAR(20) NOT NULL DEFAULT 'PRESEASON'
        CHECK(state IN ('PRESEASON','ACTIVE','SEASON_END','OFFSEASON','CONTRACT_WINDOW')),
    season_seed BIGINT NOT NULL DEFAULT floor(random()*1000000000)::BIGINT,
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    champion_club_id BIGINT REFERENCES clubs(id) ON DELETE SET NULL,
    offseason_at TIMESTAMPTZ,
    preseason_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE seasons ADD COLUMN IF NOT EXISTS season_seed BIGINT;
ALTER TABLE seasons ADD COLUMN IF NOT EXISTS offseason_at TIMESTAMPTZ;
ALTER TABLE seasons ADD COLUMN IF NOT EXISTS preseason_at TIMESTAMPTZ;
UPDATE seasons SET season_seed=floor(random()*1000000000)::BIGINT WHERE season_seed IS NULL;
ALTER TABLE seasons ALTER COLUMN season_seed SET NOT NULL;

ALTER TABLE leagues ADD COLUMN IF NOT EXISTS season_id BIGINT REFERENCES seasons(id) ON DELETE SET NULL;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS season_id BIGINT REFERENCES seasons(id) ON DELETE SET NULL;
ALTER TABLE cups ADD COLUMN IF NOT EXISTS season_id BIGINT REFERENCES seasons(id) ON DELETE SET NULL;
ALTER TABLE champions_league ADD COLUMN IF NOT EXISTS season_id BIGINT REFERENCES seasons(id) ON DELETE SET NULL;

ALTER TABLE leagues ADD COLUMN IF NOT EXISTS season_number INT NOT NULL DEFAULT 1;
ALTER TABLE leagues ADD COLUMN IF NOT EXISTS season_state VARCHAR(16) NOT NULL DEFAULT 'PRESEASON';
ALTER TABLE leagues DROP CONSTRAINT IF EXISTS leagues_season_state_check;
ALTER TABLE leagues ADD CONSTRAINT leagues_season_state_check
    CHECK(season_state IN ('PRESEASON','ACTIVE','SEASON_END','OFFSEASON','CONTRACT_WINDOW'));

CREATE TABLE IF NOT EXISTS season_history(
    id BIGSERIAL PRIMARY KEY,
    league_id BIGINT NOT NULL REFERENCES leagues(id) ON DELETE CASCADE,
    season_id BIGINT REFERENCES seasons(id) ON DELETE SET NULL,
    season_number INT NOT NULL,
    champion_club_id BIGINT REFERENCES clubs(id) ON DELETE SET NULL,
    promoted_club_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    relegated_club_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(league_id,season_number)
);
ALTER TABLE season_history ADD COLUMN IF NOT EXISTS season_id BIGINT REFERENCES seasons(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_season_history_club ON season_history(champion_club_id,season_number);
CREATE INDEX IF NOT EXISTS idx_season_history_season ON season_history(season_id,league_id);

CREATE TABLE IF NOT EXISTS player_season_state(
    player_id BIGINT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    season_id BIGINT NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
    aged BOOLEAN NOT NULL DEFAULT FALSE,
    development_applied BOOLEAN NOT NULL DEFAULT FALSE,
    contract_processed BOOLEAN NOT NULL DEFAULT FALSE,
    reward_claimed BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY(player_id,season_id)
);

-- Bootstrap one persistent season for databases upgrading from the pre-P8 schema.
INSERT INTO seasons(season_number,state,started_at)
SELECT COALESCE(MAX(season_number),1),
       CASE WHEN COUNT(*) FILTER (WHERE status='ACTIVE') > 0 THEN 'ACTIVE' ELSE 'PRESEASON' END,
       COALESCE(MIN(started_at),now())
FROM leagues
WHERE NOT EXISTS (SELECT 1 FROM seasons);

UPDATE leagues l
SET season_id=s.id
FROM seasons s
WHERE l.season_id IS NULL AND s.season_number=l.season_number;

UPDATE matches m
SET season_id=l.season_id
FROM leagues l
WHERE m.season_id IS NULL AND m.league_id=l.id;

UPDATE cups c
SET season_id=s.id
FROM seasons s
WHERE c.season_id IS NULL AND c.season=s.season_number;

UPDATE champions_league c
SET season_id=s.id
FROM seasons s
WHERE c.season_id IS NULL AND c.season=s.season_number;

UPDATE season_history h
SET season_id=l.season_id
FROM leagues l
WHERE h.season_id IS NULL AND h.league_id=l.id AND h.season_number=l.season_number;

UPDATE leagues SET season_state=CASE WHEN status='ACTIVE' THEN 'ACTIVE' ELSE COALESCE(season_state,'PRESEASON') END
WHERE season_state IS NULL OR season_state='';

CREATE INDEX IF NOT EXISTS idx_leagues_season ON leagues(season_id);
CREATE INDEX IF NOT EXISTS idx_matches_season_status ON matches(season_id,status);
CREATE INDEX IF NOT EXISTS idx_cups_season ON cups(season_id);
CREATE INDEX IF NOT EXISTS idx_champions_league_season ON champions_league(season_id);

ALTER TABLE player_season_state ADD COLUMN IF NOT EXISTS reward_claimed BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE seasons DROP CONSTRAINT IF EXISTS seasons_state_check;
ALTER TABLE seasons ADD CONSTRAINT seasons_state_check CHECK(state IN ('PRESEASON','ACTIVE','SEASON_END','OFFSEASON','CONTRACT_WINDOW'));
