-- Persist penalty shootout scores for knockout cup matches.
ALTER TABLE matches
    ADD COLUMN IF NOT EXISTS home_penalties INT NULL,
    ADD COLUMN IF NOT EXISTS away_penalties INT NULL;

ALTER TABLE cup_matches
    ADD COLUMN IF NOT EXISTS home_penalties INT NULL,
    ADD COLUMN IF NOT EXISTS away_penalties INT NULL;
