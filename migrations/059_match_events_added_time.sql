-- Explicit marker for stoppage-time events; metadata keeps the added-minute offset.
ALTER TABLE match_events
    ADD COLUMN IF NOT EXISTS is_added_time BOOLEAN NOT NULL DEFAULT FALSE;
