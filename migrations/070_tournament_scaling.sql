-- Scalable multi-instance Champions League and auditable tournament draws.
-- All changes are additive/idempotent so existing tournament history is preserved.

ALTER TABLE champions_league
    ADD COLUMN IF NOT EXISTS season_id BIGINT REFERENCES seasons(id) ON DELETE SET NULL;
ALTER TABLE champions_league
    ADD COLUMN IF NOT EXISTS tournament_index INT NOT NULL DEFAULT 1;
ALTER TABLE champions_league
    ADD COLUMN IF NOT EXISTS tournament_code VARCHAR(16) NOT NULL DEFAULT 'A';
ALTER TABLE champions_league
    ADD COLUMN IF NOT EXISTS display_name TEXT NOT NULL DEFAULT 'ЛЧ-А';

-- Previous schema allowed only one Champions League per season. Replace that
-- restriction with a unique index for each tournament instance in a season.
ALTER TABLE champions_league DROP CONSTRAINT IF EXISTS champions_league_season_key;
CREATE UNIQUE INDEX IF NOT EXISTS uq_champions_season_tournament
    ON champions_league(season, tournament_index);
CREATE INDEX IF NOT EXISTS idx_champions_season_status
    ON champions_league(season_id, status, tournament_index);

ALTER TABLE champions_matches
    ADD COLUMN IF NOT EXISTS home_penalties INT NULL;
ALTER TABLE champions_matches
    ADD COLUMN IF NOT EXISTS away_penalties INT NULL;
-- A durable snapshot of each qualified club and its draw provenance.
CREATE TABLE IF NOT EXISTS champions_participants (
    id BIGSERIAL PRIMARY KEY,
    cl_id BIGINT NOT NULL REFERENCES champions_league(id) ON DELETE CASCADE,
    club_id BIGINT NOT NULL REFERENCES clubs(id) ON DELETE CASCADE,
    source_league_id BIGINT REFERENCES leagues(id) ON DELETE SET NULL,
    source_tier VARCHAR(20) NOT NULL DEFAULT 'ROOKIE',
    qualification_position INT NOT NULL DEFAULT 99,
    qualification_season_id BIGINT REFERENCES seasons(id) ON DELETE SET NULL,
    draw_pot INT NOT NULL DEFAULT 4 CHECK (draw_pot BETWEEN 1 AND 4),
    group_name TEXT,
    group_position INT,
    qualified_as VARCHAR(24) NOT NULL DEFAULT 'TOP4',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(cl_id, club_id)
);
ALTER TABLE champions_participants ADD COLUMN IF NOT EXISTS qualification_season_id BIGINT REFERENCES seasons(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_champions_participants_group
    ON champions_participants(cl_id, group_name, draw_pot);
CREATE INDEX IF NOT EXISTS idx_champions_participants_source
    ON champions_participants(source_league_id, source_tier);

-- Persist the Cup's 64-team draw too, so the draw is reproducible and the
-- original league/division of each participant is not lost after promotion.
CREATE TABLE IF NOT EXISTS cup_participants (
    id BIGSERIAL PRIMARY KEY,
    cup_id BIGINT NOT NULL REFERENCES cups(id) ON DELETE CASCADE,
    club_id BIGINT NOT NULL REFERENCES clubs(id) ON DELETE CASCADE,
    source_league_id BIGINT REFERENCES leagues(id) ON DELETE SET NULL,
    source_tier VARCHAR(20) NOT NULL DEFAULT 'ROOKIE',
    qualification_position INT NOT NULL DEFAULT 99,
    seed_rating NUMERIC(12,3) NOT NULL DEFAULT 0,
    draw_pot INT NOT NULL DEFAULT 4 CHECK (draw_pot BETWEEN 1 AND 4),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(cup_id, club_id)
);
CREATE INDEX IF NOT EXISTS idx_cup_participants_draw
    ON cup_participants(cup_id, draw_pot, seed_rating DESC);

-- Backfill labels and season IDs for tournaments created before this migration.
UPDATE champions_league
SET display_name = 'ЛЧ-' || tournament_code
WHERE display_name IS NULL OR display_name = '' OR display_name LIKE 'Лига чемпионов%';
UPDATE champions_league cl
SET season_id = s.id
FROM seasons s
WHERE cl.season_id IS NULL AND cl.season = s.season_number;

-- Backfill participant snapshots for older tournaments so club screens continue
-- to recognize existing entrants after the new participant-based lookup ships.
INSERT INTO champions_participants
    (cl_id,club_id,source_league_id,source_tier,qualification_position,draw_pot,group_name,qualified_as)
SELECT DISTINCT cm.cl_id, entrant.club_id, lt.league_id, COALESCE(l.tier,'ROOKIE'), 99, 4, cm.group_name, 'LEGACY'
FROM champions_matches cm
CROSS JOIN LATERAL (VALUES(cm.home_club_id),(cm.away_club_id)) AS entrant(club_id)
LEFT JOIN league_teams lt ON lt.club_id=entrant.club_id
LEFT JOIN leagues l ON l.id=lt.league_id
WHERE cm.group_name LIKE 'Группа %'
ON CONFLICT(cl_id,club_id) DO NOTHING;

INSERT INTO cup_participants
    (cup_id,club_id,source_league_id,source_tier,qualification_position,seed_rating,draw_pot)
SELECT DISTINCT cm.cup_id, entrant.club_id, lt.league_id, COALESCE(l.tier,'ROOKIE'), 99,
       COALESCE(c.reputation,0), 4
FROM cup_matches cm
CROSS JOIN LATERAL (VALUES(cm.home_club_id),(cm.away_club_id)) AS entrant(club_id)
LEFT JOIN league_teams lt ON lt.club_id=entrant.club_id
LEFT JOIN leagues l ON l.id=lt.league_id
LEFT JOIN clubs c ON c.id=entrant.club_id
WHERE cm.round=1
ON CONFLICT(cup_id,club_id) DO NOTHING;
