ALTER TABLE users
    ADD COLUMN IF NOT EXISTS avatar_id INTEGER NOT NULL DEFAULT 1;

ALTER TABLE users
    DROP CONSTRAINT IF EXISTS users_avatar_id_range_check;

ALTER TABLE users
    ADD CONSTRAINT users_avatar_id_range_check CHECK (avatar_id BETWEEN 1 AND 8);
