-- Persist the manager-selected substitute bench separately from the starting XI.
ALTER TABLE club_lineups
    ADD COLUMN IF NOT EXISTS bench_ids BIGINT[] NOT NULL DEFAULT '{}';

-- Keep existing rows valid and prevent stale lineup IDs from being mistaken for bench players.
UPDATE club_lineups
SET bench_ids = ARRAY(
    SELECT bench.pid FROM unnest(COALESCE(bench_ids, '{}'::BIGINT[])) AS bench(pid)
    WHERE NOT (pid = ANY(COALESCE(player_ids, '{}'::BIGINT[])))
    LIMIT 7
)
WHERE bench_ids IS NULL OR cardinality(bench_ids) > 7
   OR bench_ids && COALESCE(player_ids, '{}'::BIGINT[]);
