-- Backfill ownership metadata for legacy club_players rows and make it mandatory.
UPDATE club_players cp
SET template_id=COALESCE(p.template_id, CASE WHEN p.is_template THEN p.id END)
FROM players p
WHERE p.id=cp.player_id
  AND cp.template_id IS NULL;

-- Any remaining NULL means the owned player is not a template copy and cannot
-- participate in the one-copy-per-template invariant. Fail migration instead
-- of silently allowing an unprotected row into production.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM club_players WHERE template_id IS NULL) THEN
        RAISE EXCEPTION 'club_players contains rows with NULL template_id; repair ownership metadata before enforcing NOT NULL';
    END IF;
END $$;

ALTER TABLE club_players ALTER COLUMN template_id SET NOT NULL;

-- Migration 022 used a partial index. Drop it explicitly because IF NOT EXISTS
-- would otherwise keep the old partial index with the same name.
DROP INDEX IF EXISTS one_copy_per_template_per_club;

-- CREATE UNIQUE INDEX one_copy_per_template_per_club (idempotent form below)
CREATE UNIQUE INDEX IF NOT EXISTS one_copy_per_template_per_club
    ON club_players(club_id, template_id);
