DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid='leagues'::regclass AND conname='leagues_max_teams_check'
    ) THEN
        ALTER TABLE leagues DROP CONSTRAINT leagues_max_teams_check;
    END IF;
END $$;

ALTER TABLE leagues
    ADD CONSTRAINT leagues_max_teams_check CHECK (max_teams BETWEEN 4 AND 16);
