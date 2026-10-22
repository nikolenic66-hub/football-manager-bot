CREATE TABLE IF NOT EXISTS shortlist (
    id BIGSERIAL PRIMARY KEY,
    club_id BIGINT NOT NULL REFERENCES clubs(id) ON DELETE CASCADE,
    player_id BIGINT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(club_id, player_id)
);
CREATE INDEX IF NOT EXISTS idx_shortlist_club_created ON shortlist(club_id, created_at DESC, id DESC);
