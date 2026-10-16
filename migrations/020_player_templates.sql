-- Player templates: immutable catalog entries and unique club-owned copies.
ALTER TABLE players ADD COLUMN IF NOT EXISTS is_seed BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE players ADD COLUMN IF NOT EXISTS is_template BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE players ADD COLUMN IF NOT EXISTS template_id BIGINT NULL REFERENCES players(id) ON DELETE RESTRICT;

UPDATE players
SET is_seed=TRUE
WHERE id<=1248
  AND data_source IN ('seed','curated_real');

UPDATE players
SET is_template=TRUE, template_id=NULL
WHERE is_seed=TRUE;

DROP INDEX IF EXISTS one_owner_per_player;
CREATE INDEX IF NOT EXISTS idx_players_template_rarity ON players(is_template, rarity);
CREATE INDEX IF NOT EXISTS idx_players_template_id ON players(template_id);
