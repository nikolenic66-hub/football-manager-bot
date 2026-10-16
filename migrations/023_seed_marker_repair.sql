-- Repair seed/template markers after the original 020 migration.
-- Admin-created players are identified explicitly by data_source='admin'.
-- Legacy admin rows created before the marker split used data_source='generated';
-- their admin_actions_log entry is the only safe historical discriminator.
UPDATE players
SET is_seed=FALSE
WHERE is_seed=TRUE
  AND (
      data_source='admin'
      OR EXISTS (
          SELECT 1
          FROM admin_actions_log l
          WHERE l.action='player_create'
            AND l.target_type='player'
            AND l.target_id=players.id
      )
  );

UPDATE players
SET is_seed=TRUE
WHERE is_seed=FALSE
  AND template_id IS NULL
  AND data_source IN ('seed','curated_real');

-- Legacy pre-020 synthetic seed rows used data_source='generated'. Promote only
-- seed-range rows that have no admin creation audit record. This preserves old
-- installations while keeping admin-created rows out of the seed pool.
UPDATE players
SET is_seed=TRUE
WHERE is_seed=FALSE
  AND template_id IS NULL
  AND id<=1248
  AND data_source='generated'
  AND NOT EXISTS (
      SELECT 1
      FROM admin_actions_log l
      WHERE l.action='player_create'
        AND l.target_type='player'
        AND l.target_id=players.id
  );

UPDATE players
SET is_template=TRUE,
    template_id=NULL
WHERE is_seed=TRUE
   OR (data_source='admin' AND template_id IS NULL)
   OR (template_id IS NULL AND EXISTS (
       SELECT 1
       FROM admin_actions_log l
       WHERE l.action='player_create'
         AND l.target_type='player'
         AND l.target_id=players.id
       ));

-- Physical copies are never templates. Do not blanket-clear non-seed templates:
-- administrator-created players are legitimate non-seed templates.
UPDATE players
SET is_template=FALSE,
    template_id=NULL
WHERE is_seed=FALSE
  AND template_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_players_data_source ON players(data_source);
