-- Repair the player identity sequence after manual seeded IDs and undo the legacy 018
-- name mutation for curated real players on databases where 018 was already applied.
UPDATE players
SET last_name=''
WHERE real_player IS TRUE
  AND last_name = id::text;

SELECT setval(
    'players_id_seq',
    COALESCE((SELECT MAX(id) FROM players), 1),
    (SELECT COUNT(*) > 0 FROM players)
);
