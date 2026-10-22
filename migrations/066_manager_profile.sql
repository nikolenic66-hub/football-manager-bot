ALTER TABLE users
    ADD COLUMN IF NOT EXISTS manager_name TEXT;

CREATE INDEX IF NOT EXISTS idx_users_manager_name
    ON users(manager_name)
    WHERE manager_name IS NOT NULL;
