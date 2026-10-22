-- Re-apply the bench column even if a database incorrectly marked migration
-- 067 as applied. Safe for fresh installs and repeated deployments.
CREATE TABLE IF NOT EXISTS club_lineups (
    club_id BIGINT PRIMARY KEY REFERENCES clubs(id) ON DELETE CASCADE,
    player_ids BIGINT[] NOT NULL DEFAULT '{}'::BIGINT[]
);
ALTER TABLE club_lineups
    ADD COLUMN IF NOT EXISTS bench_ids BIGINT[] NOT NULL DEFAULT '{}'::BIGINT[];
