-- Normalize the club-player ownership index for deployments that may still have
-- the legacy partial index from migration 022. Migration 026 makes template_id
-- mandatory, so every club_players row participates in this invariant.
DELETE FROM club_players cp
WHERE cp.id NOT IN (
    SELECT MIN(id) FROM club_players
    GROUP BY club_id, template_id
);

DROP INDEX IF EXISTS one_copy_per_template_per_club;

-- CREATE UNIQUE INDEX one_copy_per_template_per_club (idempotent form below)
CREATE UNIQUE INDEX IF NOT EXISTS one_copy_per_template_per_club
    ON club_players (club_id, template_id);
