-- Force identity sequence synchronization after manual/seeded IDs.
-- Safe and idempotent: may be applied repeatedly.
SELECT setval(
    'players_id_seq',
    COALESCE((SELECT MAX(id) FROM players), 1),
    (SELECT COUNT(*) > 0 FROM players)
);
