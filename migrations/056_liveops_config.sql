CREATE TABLE IF NOT EXISTS liveops_season_config(
    id BIGSERIAL PRIMARY KEY,
    season_id BIGINT NOT NULL UNIQUE REFERENCES seasons(id) ON DELETE CASCADE,
    config JSONB NOT NULL DEFAULT '{}'::jsonb,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_liveops_config_enabled ON liveops_season_config(enabled,season_id);

CREATE TABLE IF NOT EXISTS liveops_events(
    id BIGSERIAL PRIMARY KEY,
    season_id BIGINT REFERENCES seasons(id) ON DELETE CASCADE,
    event_key TEXT NOT NULL,
    starts_at TIMESTAMPTZ NOT NULL,
    ends_at TIMESTAMPTZ NOT NULL,
    config JSONB NOT NULL DEFAULT '{}'::jsonb,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE(season_id,event_key),
    CHECK(ends_at>starts_at)
);
CREATE INDEX IF NOT EXISTS idx_liveops_events_window ON liveops_events(starts_at,ends_at,enabled);
CREATE TABLE IF NOT EXISTS liveops_daily_quest_definitions(
    id BIGSERIAL PRIMARY KEY,
    quest_type VARCHAR(32) NOT NULL,
    title TEXT NOT NULL,
    target INT NOT NULL CHECK(target>0),
    reward JSONB NOT NULL,
    sort_order INT NOT NULL DEFAULT 0,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE(quest_type)
);
INSERT INTO liveops_daily_quest_definitions(quest_type,title,target,reward,sort_order)
VALUES
('PLAY_MATCH','Сыграй матч',1,'{"type":"bp_xp","amount":10,"label":"10 BP XP"}'::jsonb,10),
('SCORE_GOALS','Забей 3 гола',3,'{"type":"money","amount":25000,"label":"€25,000"}'::jsonb,20),
('CHANGE_TACTICS','Поменяй тактику',1,'{"type":"item","item":"MEDKIT","qty":1,"label":"1 MEDKIT"}'::jsonb,30)
ON CONFLICT(quest_type) DO NOTHING;
