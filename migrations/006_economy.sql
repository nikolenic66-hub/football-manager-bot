ALTER TABLE clubs ADD COLUMN IF NOT EXISTS ticket_price INT NOT NULL DEFAULT 20 CHECK(ticket_price BETWEEN 5 AND 120);
ALTER TABLE clubs ADD COLUMN IF NOT EXISTS sponsor_level INT NOT NULL DEFAULT 1 CHECK(sponsor_level BETWEEN 1 AND 10);

CREATE TABLE IF NOT EXISTS club_financial_transactions(
 id BIGSERIAL PRIMARY KEY,
 club_id BIGINT NOT NULL REFERENCES clubs(id) ON DELETE CASCADE,
 league_id BIGINT REFERENCES leagues(id) ON DELETE SET NULL,
 match_id BIGINT REFERENCES matches(id) ON DELETE SET NULL,
 category TEXT NOT NULL CHECK(category IN ('TICKETS','SPONSOR','WAGES','STADIUM','TRANSFER_IN','TRANSFER_OUT','PRIZE','OTHER')),
 amount BIGINT NOT NULL,
 balance_after BIGINT NOT NULL CHECK(balance_after>=0),
 description TEXT NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_finance_club_created ON club_financial_transactions(club_id,created_at DESC,id DESC);
CREATE INDEX IF NOT EXISTS idx_finance_league ON club_financial_transactions(league_id,category);

CREATE TABLE IF NOT EXISTS club_season_finances(
 league_id BIGINT NOT NULL REFERENCES leagues(id) ON DELETE CASCADE,
 club_id BIGINT NOT NULL REFERENCES clubs(id) ON DELETE CASCADE,
 ticket_revenue BIGINT NOT NULL DEFAULT 0,
 sponsor_revenue BIGINT NOT NULL DEFAULT 0,
 prize_money BIGINT NOT NULL DEFAULT 0,
 wages BIGINT NOT NULL DEFAULT 0,
 stadium_costs BIGINT NOT NULL DEFAULT 0,
 transfer_spend BIGINT NOT NULL DEFAULT 0,
 transfer_income BIGINT NOT NULL DEFAULT 0,
 other_income BIGINT NOT NULL DEFAULT 0,
 other_expense BIGINT NOT NULL DEFAULT 0,
 PRIMARY KEY(league_id,club_id)
);

