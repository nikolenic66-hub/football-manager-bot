CREATE TABLE IF NOT EXISTS packs(
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    price_coins BIGINT NOT NULL CHECK(price_coins>=0),
    price_stars INT NOT NULL DEFAULT 0 CHECK(price_stars>=0),
    min_ovr INT NOT NULL CHECK(min_ovr BETWEEN 1 AND 100),
    max_ovr INT NOT NULL CHECK(max_ovr BETWEEN 1 AND 100),
    CHECK(min_ovr<=max_ovr)
);

CREATE TABLE IF NOT EXISTS pack_history(
    id BIGSERIAL PRIMARY KEY,
    club_id BIGINT NOT NULL REFERENCES clubs(id) ON DELETE CASCADE,
    pack_id BIGINT NOT NULL REFERENCES packs(id) ON DELETE RESTRICT,
    player_id BIGINT NOT NULL REFERENCES players(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_pack_history_club_created ON pack_history(club_id,created_at DESC,id DESC);

INSERT INTO packs(name,price_coins,price_stars,min_ovr,max_ovr) VALUES
('Бронзовый',250000,0,50,65),
('Серебряный',750000,0,60,75),
('Золотой',2500000,0,70,85),
('Легендарный',10000000,0,80,95)
ON CONFLICT(name) DO UPDATE SET
    price_coins=EXCLUDED.price_coins,
    price_stars=EXCLUDED.price_stars,
    min_ovr=EXCLUDED.min_ovr,
    max_ovr=EXCLUDED.max_ovr;
