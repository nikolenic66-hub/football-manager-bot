CREATE TABLE IF NOT EXISTS daily_quests(
    id BIGSERIAL PRIMARY KEY,
    club_id BIGINT NOT NULL REFERENCES clubs(id) ON DELETE CASCADE,
    quest_date DATE NOT NULL DEFAULT CURRENT_DATE,
    quest_type VARCHAR(32) NOT NULL,
    progress INT NOT NULL DEFAULT 0 CHECK(progress>=0),
    target INT NOT NULL CHECK(target>0),
    reward JSONB NOT NULL DEFAULT '{}'::jsonb,
    status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE','COMPLETED','CLAIMED','EXPIRED')),
    UNIQUE(club_id,quest_date,quest_type)
);
CREATE INDEX IF NOT EXISTS idx_daily_quests_club_date ON daily_quests(club_id,quest_date,id);

CREATE TABLE IF NOT EXISTS club_inventory(
    club_id BIGINT NOT NULL REFERENCES clubs(id) ON DELETE CASCADE,
    item_type VARCHAR(64) NOT NULL,
    quantity INT NOT NULL DEFAULT 0 CHECK(quantity>=0),
    PRIMARY KEY(club_id,item_type)
);
