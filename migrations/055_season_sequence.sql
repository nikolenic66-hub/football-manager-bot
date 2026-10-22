-- P8.2: persistent season sequence and immutable per-season league snapshots.
CREATE TABLE IF NOT EXISTS season_sequences(
    id BIGSERIAL PRIMARY KEY,
    root_seed BIGINT NOT NULL,
    current_season_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE seasons ADD COLUMN IF NOT EXISTS sequence_id BIGINT REFERENCES season_sequences(id) ON DELETE RESTRICT;

-- Bootstrap one sequence for existing databases. The first season seed becomes
-- the stable root seed for the already-running career/world.
INSERT INTO season_sequences(root_seed, current_season_id)
SELECT COALESCE((SELECT season_seed FROM seasons ORDER BY id LIMIT 1), floor(random()*1000000000)::BIGINT),
       (SELECT id FROM seasons ORDER BY id DESC LIMIT 1)
WHERE NOT EXISTS (SELECT 1 FROM season_sequences);

UPDATE seasons s
SET sequence_id=ss.id
FROM (SELECT id FROM season_sequences ORDER BY id LIMIT 1) ss
WHERE s.sequence_id IS NULL;

ALTER TABLE seasons ALTER COLUMN sequence_id SET NOT NULL;
ALTER TABLE seasons DROP CONSTRAINT IF EXISTS seasons_season_number_key;
CREATE UNIQUE INDEX IF NOT EXISTS uq_seasons_sequence_number ON seasons(sequence_id,season_number);
CREATE INDEX IF NOT EXISTS idx_seasons_sequence ON seasons(sequence_id,id);

UPDATE season_sequences ss
SET current_season_id=(SELECT s.id FROM seasons s WHERE s.sequence_id=ss.id ORDER BY s.id DESC LIMIT 1);

CREATE TABLE IF NOT EXISTS season_league_snapshots(
    id BIGSERIAL PRIMARY KEY,
    sequence_id BIGINT NOT NULL REFERENCES season_sequences(id) ON DELETE RESTRICT,
    season_id BIGINT NOT NULL REFERENCES seasons(id) ON DELETE RESTRICT,
    league_id BIGINT NOT NULL REFERENCES leagues(id) ON DELETE RESTRICT,
    league_name TEXT NOT NULL,
    tier VARCHAR(20),
    season_number INT NOT NULL,
    standings JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(season_id,league_id)
);
CREATE INDEX IF NOT EXISTS idx_season_league_snapshots_sequence ON season_league_snapshots(sequence_id,season_number);
CREATE INDEX IF NOT EXISTS idx_season_league_snapshots_club ON season_league_snapshots USING GIN(standings);

CREATE OR REPLACE FUNCTION prevent_season_league_snapshot_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'season_league_snapshots are immutable';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_season_league_snapshots_immutable ON season_league_snapshots;
CREATE TRIGGER trg_season_league_snapshots_immutable
BEFORE UPDATE OR DELETE ON season_league_snapshots
FOR EACH ROW EXECUTE FUNCTION prevent_season_league_snapshot_mutation();
