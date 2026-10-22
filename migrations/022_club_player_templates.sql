-- Keep the source template on each club-owned copy and enforce one copy per template per club.
ALTER TABLE club_players ADD COLUMN IF NOT EXISTS template_id BIGINT NULL REFERENCES players(id) ON DELETE RESTRICT;

UPDATE club_players cp
SET template_id=p.template_id
FROM players p
WHERE p.id=cp.player_id AND p.template_id IS NOT NULL;

-- Keep the physical player with the smallest player_id when legacy data contains
-- duplicate copies of the same template in one club. This is one SQL statement,
-- so the migration runner never relies on PostgreSQL multi-statement execution.
DELETE FROM club_players cp
WHERE cp.template_id IS NOT NULL
  AND EXISTS (
      SELECT 1
      FROM club_players keep
      WHERE keep.club_id=cp.club_id
        AND keep.template_id=cp.template_id
        AND keep.player_id < cp.player_id
  );

CREATE UNIQUE INDEX IF NOT EXISTS one_copy_per_template_per_club
    ON club_players(club_id, template_id);
