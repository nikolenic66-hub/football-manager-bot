ALTER TABLE club_players ADD COLUMN IF NOT EXISTS morale_boost_matches INT NOT NULL DEFAULT 0 CHECK(morale_boost_matches>=0);
ALTER TABLE club_players ADD COLUMN IF NOT EXISTS morale_boost_amount INT NOT NULL DEFAULT 0 CHECK(morale_boost_amount BETWEEN 0 AND 20);
