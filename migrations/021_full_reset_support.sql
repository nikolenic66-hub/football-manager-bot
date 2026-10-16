-- Manual full-reset support only. No reset is executed by migration/deploy.
CREATE INDEX IF NOT EXISTS idx_players_template_rarity_position
    ON players(is_template, rarity, position);
