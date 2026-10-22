-- P6/P8 tournament structure: global 64-team cup.
ALTER TABLE cups ALTER COLUMN league_id DROP NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_cups_global_season ON cups(season) WHERE league_id IS NULL;
