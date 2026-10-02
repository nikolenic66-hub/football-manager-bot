ALTER TABLE club_players ADD COLUMN IF NOT EXISTS contract_salary BIGINT;
ALTER TABLE club_players ADD COLUMN IF NOT EXISTS release_clause BIGINT NOT NULL DEFAULT 0;
ALTER TABLE club_players ADD COLUMN IF NOT EXISTS morale INT NOT NULL DEFAULT 70 CHECK(morale BETWEEN 0 AND 100);
ALTER TABLE club_players ADD COLUMN IF NOT EXISTS playing_time_expectation TEXT NOT NULL DEFAULT 'SQUAD' CHECK(playing_time_expectation IN ('STAR','KEY','SQUAD','PROSPECT'));
ALTER TABLE club_players ADD COLUMN IF NOT EXISTS appearances INT NOT NULL DEFAULT 0 CHECK(appearances>=0);
ALTER TABLE club_players ADD COLUMN IF NOT EXISTS starts INT NOT NULL DEFAULT 0 CHECK(starts>=0);
ALTER TABLE club_players ADD COLUMN IF NOT EXISTS minutes_played INT NOT NULL DEFAULT 0 CHECK(minutes_played>=0);
ALTER TABLE club_players ADD COLUMN IF NOT EXISTS training_focus TEXT NOT NULL DEFAULT 'BALANCED';
ALTER TABLE club_players ADD COLUMN IF NOT EXISTS training_intensity TEXT NOT NULL DEFAULT 'NORMAL';
ALTER TABLE club_players ADD COLUMN IF NOT EXISTS development_points INT NOT NULL DEFAULT 0 CHECK(development_points>=0);
ALTER TABLE club_players ADD COLUMN IF NOT EXISTS last_training_at TIMESTAMPTZ;
UPDATE club_players cp SET contract_salary=COALESCE(cp.contract_salary,p.salary), release_clause=CASE WHEN cp.release_clause=0 THEN GREATEST(p.market_value*2,p.salary*36) ELSE cp.release_clause END FROM players p WHERE p.id=cp.player_id;

CREATE TABLE IF NOT EXISTS transfer_listings(
 id BIGSERIAL PRIMARY KEY,
 player_id BIGINT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
 seller_club_id BIGINT REFERENCES clubs(id) ON DELETE CASCADE,
 asking_price BIGINT NOT NULL CHECK(asking_price>=0),
 minimum_price BIGINT NOT NULL CHECK(minimum_price>=0),
 status TEXT NOT NULL DEFAULT 'LISTED' CHECK(status IN ('LISTED','SOLD','CANCELLED')),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_active_transfer_listing ON transfer_listings(player_id) WHERE status='LISTED';
CREATE INDEX IF NOT EXISTS idx_transfer_listings_status ON transfer_listings(status,asking_price);

CREATE TABLE IF NOT EXISTS transfer_offers(
 id BIGSERIAL PRIMARY KEY,
 listing_id BIGINT NOT NULL REFERENCES transfer_listings(id) ON DELETE CASCADE,
 buyer_club_id BIGINT NOT NULL REFERENCES clubs(id) ON DELETE CASCADE,
 amount BIGINT NOT NULL CHECK(amount>0),
 status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','ACCEPTED','REJECTED','WITHDRAWN')),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_transfer_offers_buyer ON transfer_offers(buyer_club_id,status);

CREATE TABLE IF NOT EXISTS club_training(
 club_id BIGINT PRIMARY KEY REFERENCES clubs(id) ON DELETE CASCADE,
 focus TEXT NOT NULL DEFAULT 'BALANCED',
 intensity TEXT NOT NULL DEFAULT 'NORMAL',
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS player_development_log(
 id BIGSERIAL PRIMARY KEY,
 club_id BIGINT REFERENCES clubs(id) ON DELETE SET NULL,
 player_id BIGINT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
 focus TEXT NOT NULL,
 points INT NOT NULL,
 changes JSONB NOT NULL DEFAULT '{}'::jsonb,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

DROP TABLE IF EXISTS supporter_payments;
ALTER TABLE users DROP COLUMN IF EXISTS supporter_tier;
