from __future__ import annotations
import json
from sqlalchemy import text

DEFAULT_CONFIG = {
    'match_ticket_multiplier': 1.0,
    'sponsor_multiplier': 1.0,
    'wage_multiplier': 1.0,
    'prize_multiplier': 1.0,
    'battle_pass_xp_multiplier': 1.0,
    'quest_reward_multiplier': 1.0,
    'pack_price_multiplier': 1.0,
}

async def get_liveops_config(session, season_id=None):
    if season_id is None:
        season_id = (await session.execute(text("SELECT id FROM seasons WHERE state IN ('ACTIVE','CONTRACT_WINDOW','OFFSEASON') ORDER BY id DESC LIMIT 1"))).scalar_one_or_none()
    if season_id is None:
        return dict(DEFAULT_CONFIG)
    row = (await session.execute(text("""
        SELECT config FROM liveops_season_config WHERE season_id=:s AND enabled=TRUE
        ORDER BY id DESC LIMIT 1
    """), {'s': int(season_id)})).scalar_one_or_none()
    cfg = dict(DEFAULT_CONFIG)
    if row:
        cfg.update({k: float(v) for k, v in dict(row).items() if k in cfg})
    return cfg

async def ensure_liveops_config(session, season_id, config=None):
    cfg = dict(DEFAULT_CONFIG)
    if config:
        cfg.update({k: float(v) for k, v in config.items() if k in cfg})
    await session.execute(text("""
        INSERT INTO liveops_season_config(season_id,config,enabled)
        VALUES(:s,CAST(:c AS jsonb),TRUE)
        ON CONFLICT(season_id) DO NOTHING
    """), {'s': int(season_id), 'c': json.dumps(cfg, ensure_ascii=False)})
    return await get_liveops_config(session, int(season_id))

async def seed_liveops_for_season(session, season_id):
    return await ensure_liveops_config(session, season_id)


def multiplier(config, key):
    value = float(config.get(key, 1.0))
    return max(0.0, min(10.0, value))
