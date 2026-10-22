ALTER TABLE club_players
    ADD COLUMN IF NOT EXISTS fitness_recovered_on DATE NOT NULL DEFAULT (CURRENT_DATE - 1);

ALTER TABLE club_players
    DROP CONSTRAINT IF EXISTS club_players_morale_boost_amount_check;
ALTER TABLE club_players
    ADD CONSTRAINT club_players_morale_boost_amount_check
    CHECK (morale_boost_amount BETWEEN 0 AND 30);
