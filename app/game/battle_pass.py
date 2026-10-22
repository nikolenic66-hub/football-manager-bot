from datetime import datetime, timedelta, timezone
import logging
from types import SimpleNamespace

from sqlalchemy import text
from app.game.event_queue import queue_event
from app.game.liveops import get_liveops_config, multiplier, seed_liveops_for_season
from app.game.inventory import add_inventory_item

from app.game.ratings import player_rating
from app.services import add_copy_to_club, display_player_name, record_financial_transaction

import logging  # Explicit import before logger initialization

logger = logging.getLogger(__name__)


REWARDS = {
    1: {'type': 'money', 'amount': 50000, 'label': '€50,000'},
    3: {'type': 'item', 'item': 'MEDKIT', 'qty': 1, 'label': '1 Аптечка (MEDKIT)'},
    5: {'type': 'money', 'amount': 100000, 'label': '€100,000'},
    7: {'type': 'item', 'item': 'MORALE_BOOST', 'qty': 1, 'label': '1 Усилитель морали'},
    10: {'type': 'item', 'item': 'STAMINA_BOOST', 'qty': 1, 'label': '1 Усилитель выносливости'},
    15: {'type': 'player', 'min_ovr': 75, 'max_ovr': 80, 'label': 'Эпический игрок (OVR 75–80)'},
    20: {'type': 'money', 'amount': 250000, 'label': '€250,000'},
    25: {'type': 'bundle', 'items': [{'item': 'MEDKIT', 'qty': 2}, {'item': 'MORALE_BOOST', 'qty': 1}], 'label': '2 Аптечки + 1 Усилитель морали'},
    30: {'type': 'player', 'min_ovr': 85, 'max_ovr': 100, 'label': 'Легендарный игрок (OVR 85+)'},
}


def reward_for_level(level):
    return REWARDS.get(int(level))


def level_for_points(points):
    """Return level from cumulative progressive point thresholds."""
    remaining = max(0, int(points))
    level = 0
    for next_level in range(1, 31):
        if next_level == 1:
            threshold = 50
        elif next_level <= 5:
            threshold = 50 + (next_level - 1) * 10
        elif next_level <= 10:
            threshold = 100
        elif next_level <= 20:
            threshold = 120
        else:
            threshold = 150
        if remaining < threshold:
            break
        remaining -= threshold
        level = next_level
    return level


async def get_active_season(session):
    return (await session.execute(text("""
        SELECT id,season_id,start_date,end_date,status
        FROM season_pass
        WHERE status='ACTIVE' AND start_date<=now() AND end_date>now()
        ORDER BY id DESC LIMIT 1
    """))).mappings().first()


async def ensure_progress(session, club_id, season_pass_id):
    await session.execute(text("""
        INSERT INTO season_pass_progress(club_id,season_pass_id,points,level,claimed_rewards)
        VALUES(:c,:s,0,0,'{}'::jsonb)
        ON CONFLICT(club_id,season_pass_id) DO NOTHING
    """), {'c': int(club_id), 's': int(season_pass_id)})
    progress = (await session.execute(text("""
        SELECT id,club_id,season_pass_id,points,level,claimed_rewards
        FROM season_pass_progress
        WHERE club_id=:c AND season_pass_id=:s
        FOR UPDATE
    """), {'c': int(club_id), 's': int(season_pass_id)})).mappings().one()
    # Recalculate level while preserving points and claimed rewards.
    recalculated_level = level_for_points(progress['points'])
    if int(progress['level']) != recalculated_level:
        await session.execute(text(
            'UPDATE season_pass_progress SET level=:level WHERE id=:id'
        ), {'level': recalculated_level, 'id': int(progress['id'])})
        progress = dict(progress, level=recalculated_level)
    return progress


async def add_battle_pass_points(club_id, points, session):
    season = await get_active_season(session)
    if not season:
        return None
    progress = await ensure_progress(session, club_id, season['id'])
    cfg = await get_liveops_config(session, int(season['season_id']))
    effective_points = int(round(int(points) * multiplier(cfg, 'battle_pass_xp_multiplier')))
    new_points = max(0, int(progress['points']) + effective_points)
    new_level = level_for_points(new_points)
    await session.execute(text("""
        UPDATE season_pass_progress
        SET points=:points, level=:level
        WHERE id=:id
    """), {'points': new_points, 'level': new_level, 'id': int(progress['id'])})
    return dict(progress, points=new_points, level=new_level, season_pass=dict(season), applied_points=effective_points)


async def award_match_points(session, home_club_id, away_club_id, home_score, away_score):
    home_score, away_score = int(home_score), int(away_score)

    def match_points(scored, conceded):
        # Participation + result bonus + one point per goal + clean sheet.
        result_bonus = 3 if scored > conceded else 2 if scored == conceded else 1
        clean_sheet = 2 if conceded == 0 else 0
        return 1 + result_bonus + scored + clean_sheet

    await add_battle_pass_points(home_club_id, match_points(home_score, away_score), session)
    await add_battle_pass_points(away_club_id, match_points(away_score, home_score), session)


def _player_ovr(row):
    player = SimpleNamespace(
        id=int(row['id']), position=str(row['position']),
        pace=float(row['pace'] or 0), shooting=float(row['shooting'] or 0),
        passing=float(row['passing'] or 0), dribbling=float(row['dribbling'] or 0),
        defending=float(row['defending'] or 0), physical=float(row['physical'] or 0),
        stamina=float(row['stamina'] or 0), mental=float(row['mental'] or 0),
        fitness=100, form=0, morale=70,
    )
    return int(round(float(player_rating(player))))


async def _pick_player_reward(session, club_id, min_ovr, max_ovr):
    rows = (await session.execute(text("""
        SELECT id,first_name,last_name,position,pace,shooting,passing,dribbling,
               defending,physical,stamina,mental
        FROM players
        WHERE is_template=TRUE
        ORDER BY id
    """))).mappings().all()
    rows = [r for r in rows if min_ovr <= _player_ovr(r) <= max_ovr]
    for row in rows:
        owned = (await session.execute(text("""
            SELECT 1 FROM club_players WHERE club_id=:c AND template_id=:t LIMIT 1
        """), {'c': int(club_id), 't': int(row['id'])})).first()
        if owned is None:
            player_id, _ = await add_copy_to_club(session, int(club_id), int(row['id']))
            if player_id:
                return {
                    'player_id': int(player_id),
                    'name': display_player_name(row['first_name'], row['last_name']),
                    'position': row['position'], 'ovr': _player_ovr(row),
                }
    raise ValueError('Нет свободной карточки для награды этого уровня.')


async def claim_battle_pass_reward(session, club_id, level):
    season = await get_active_season(session)
    if not season:
        raise ValueError('Активного сезонного пропуска нет.')
    progress = await ensure_progress(session, club_id, season['id'])
    level = int(level)
    rewards = await get_season_rewards(session, season['id'])
    reward = rewards.get(level)
    if not reward:
        raise ValueError('На этом уровне награды нет.')
    if int(progress['level']) < level:
        raise ValueError('Уровень ещё не достигнут.')
    claimed = dict(progress['claimed_rewards'] or {})
    if str(level) in claimed:
        raise ValueError('Эта награда уже забрана.')

    result = dict(reward)
    if reward['type'] == 'money':
        await record_financial_transaction(session, club_id, 'OTHER', int(reward['amount']), f'Награда Battle Pass · уровень {level}')
    elif reward['type'] == 'player':
        if reward.get('player_id'):
            row = (await session.execute(text("SELECT id,first_name,last_name,position,pace,shooting,passing,dribbling,defending,physical,stamina,mental FROM players WHERE id=:id"), {'id': int(reward['player_id'])})).mappings().first()
            if not row:
                raise ValueError('Игрок для награды больше не существует.')
            player_id, _ = await add_copy_to_club(session, int(club_id), int(row['id']))
            if not player_id:
                raise ValueError('Игрок уже есть у клуба.')
            result['player'] = {'player_id': int(player_id), 'name': display_player_name(row['first_name'], row['last_name']), 'position': row['position'], 'ovr': _player_ovr(row)}
        else:
            result['player'] = await _pick_player_reward(session, club_id, reward['min_ovr'], reward['max_ovr'])
    elif reward['type'] == 'item':
        await add_inventory_item(session, int(club_id), reward['item'], int(reward.get('qty', 1)))
    elif reward['type'] == 'bundle':
        for item in reward.get('items', []):
            await add_inventory_item(session, int(club_id), item['item'], int(item.get('qty', 1)))

    claimed[str(level)] = result
    await session.execute(text("""
        UPDATE season_pass_progress SET claimed_rewards=CAST(:claimed AS jsonb) WHERE id=:id
    """), {'claimed': __import__('json').dumps(claimed, ensure_ascii=False), 'id': int(progress['id'])})
    return result


async def start_season(session, days=31, season_id=None):
    # Serialize admin double-clicks / concurrent season starts.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext('battle-pass-start'))")
    )

    # LiveOps config references seasons.id via a foreign key. Never use the
    # independent season_pass counter for liveops_season_config.season_id.
    real_season_id = int(season_id) if season_id is not None else (
        await session.execute(text(
            "SELECT id FROM seasons WHERE state='ACTIVE' "
            "ORDER BY id DESC LIMIT 1"
        ))
    ).scalar_one_or_none()
    if real_season_id is None:
        real_season_id = (
            await session.execute(text(
                "SELECT id FROM seasons ORDER BY id DESC LIMIT 1"
            ))
        ).scalar_one_or_none()
    if real_season_id is None:
        raise RuntimeError(
            'Battle Pass требует существующий сезон в таблице '
            'seasons, но ни одного нет. Вызовите '
            'ensure_initial_season перед start_season.'
        )
    real_season_id = int(real_season_id)

    now = datetime.now(timezone.utc)
    start = now
    end = now + timedelta(days=int(days))
    # season_pass.season_id is an independent Battle Pass counter.
    next_season = int(
        (await session.execute(
            text('SELECT COALESCE(MAX(season_id),0)+1 FROM season_pass')
        )).scalar_one()
    )
    await session.execute(text("""
        UPDATE season_pass SET status='FINISHED' WHERE status='ACTIVE'
    """))
    row = (await session.execute(text("""
        INSERT INTO season_pass(season_id,start_date,end_date,status)
        VALUES(:season_id,:start_date,:end_date,'ACTIVE')
        RETURNING id,season_id,start_date,end_date,status
    """), {
        'season_id': next_season,
        # asyncpg expects datetime objects for timestamptz parameters, not ISO strings.
        'start_date': start,
        'end_date': end,
    })).mappings().one()

    await seed_liveops_for_season(session, real_season_id)

    await queue_event(session, 'BATTLE_PASS_END', end, {
        'season_id': int(row['season_id']), 'season_pass_id': int(row['id']),
    })

    # 046_battle_pass_rewards.sql owns this schema.  Seed the default reward
    # set when a new season is created so the reward editor/claim flow never
    # starts against an empty season.
    for level, reward in REWARDS.items():
        await session.execute(text("""
            INSERT INTO season_pass_rewards(season_pass_id,level,reward_type,reward_value)
            VALUES(:season,:level,:type,CAST(:value AS jsonb))
            ON CONFLICT(season_pass_id,level) DO NOTHING
        """), {
            'season': int(row['id']),
            'level': int(level),
            'type': str(reward['type']),
            'value': json.dumps(reward, ensure_ascii=False),
        })
    result = dict(row)
    result['season_id'] = real_season_id
    return result

import json
import logging
import random

REWARD_LEVELS = (1, 3, 5, 7, 10, 15, 20, 25, 30)


def _money(amount):
    return {'type': 'money', 'amount': int(amount), 'label': f'€{int(amount):,}'.replace(',', ' ')}


def _item(item, qty=1):
    labels = {'MEDKIT': 'Аптечка', 'MORALE_BOOST': 'Усилитель морали', 'STAMINA_BOOST': 'Усилитель выносливости'}
    label = f'{int(qty)} {labels.get(item, item)}'
    return {'type': 'item', 'item': item, 'qty': int(qty), 'label': label}


async def _random_player_reward(session, min_ovr, max_ovr, rarity):
    rows = (await session.execute(text("""
        SELECT id,first_name,last_name,position,pace,shooting,passing,dribbling,
               defending,physical,stamina,mental
        FROM players
        WHERE is_template=TRUE AND rarity=:rarity
        ORDER BY id
    """), {'rarity': rarity})).mappings().all()
    candidates = [r for r in rows if min_ovr <= _player_ovr(r) <= max_ovr]
    if not candidates:
        raise ValueError(f'Нет шаблонного игрока {rarity} в требуемом диапазоне OVR.')
    row = random.choice(candidates)
    name = display_player_name(row['first_name'], row['last_name'])
    return {
        'type': 'player', 'player_id': int(row['id']), 'rarity': rarity,
        'min_ovr': int(min_ovr), 'max_ovr': int(max_ovr),
        'name': name, 'position': row['position'], 'ovr': _player_ovr(row),
        'label': f'{name} · {row["position"]} · OVR {_player_ovr(row)}',
    }


async def _random_rewards(session):
    return {
        1: _money(random.choice((50000, 75000))),
        3: _item(random.choice(('MEDKIT', 'MORALE_BOOST'))),
        5: _money(random.choice((100000, 150000))),
        7: _item(random.choice(('MORALE_BOOST', 'STAMINA_BOOST'))),
        10: _item(random.choice(('STAMINA_BOOST', 'MEDKIT'))),
        15: await _random_player_reward(session, 75, 80, 'EPIC'),
        20: _money(random.choice((250000, 300000))),
        25: _item(random.choice(('MEDKIT', 'MORALE_BOOST')), 2),
        30: await _random_player_reward(session, 85, 100, 'LEGENDARY'),
    }


async def _store_rewards(session, season_pass_id, rewards):
    for level, reward in rewards.items():
        await session.execute(text("""
            INSERT INTO season_pass_rewards(season_pass_id,level,reward_type,reward_value)
            VALUES(:s,:l,:t,CAST(:v AS jsonb))
            ON CONFLICT(season_pass_id,level) DO UPDATE SET reward_type=EXCLUDED.reward_type,reward_value=EXCLUDED.reward_value
        """), {
            's': int(season_pass_id), 'l': int(level), 't': reward['type'],
            'v': json.dumps(reward, ensure_ascii=False),
        })


async def get_season_rewards(session, season_pass_id):
    rows = (await session.execute(text("""
        SELECT level,reward_value FROM season_pass_rewards
        WHERE season_pass_id=:s ORDER BY level
    """), {'s': int(season_pass_id)})).mappings().all()
    if rows:
        return {int(r['level']): dict(r['reward_value']) for r in rows}
    return {int(k): dict(v) for k, v in REWARDS.items()}


async def reset_battle_pass(session, days=31, season_id=None):
    logger.info('reset_battle_pass: START days=%s', days)
    try:
        season = await start_season(session, days=days, season_id=season_id)
        logger.info(
            'reset_battle_pass: start_season OK season_id=%r',
            season.get('id') if isinstance(season, dict) else season
        )
    except Exception:
        logger.exception('reset_battle_pass: start_season FAILED')
        raise
    try:
        rewards = await _random_rewards(session)
        logger.info(
            'reset_battle_pass: _random_rewards OK, count=%d',
            len(rewards) if rewards else 0
        )
    except Exception:
        logger.exception('reset_battle_pass: _random_rewards FAILED')
        raise
    try:
        await _store_rewards(session, season['id'], rewards)
        logger.info(
            'reset_battle_pass: _store_rewards OK season_id=%r',
            season.get('id') if isinstance(season, dict) else season
        )
    except Exception:
        logger.exception(
            'reset_battle_pass: _store_rewards FAILED season_id=%r',
            season.get('id') if isinstance(season, dict) else season
        )
        raise
    return dict(season, rewards=rewards)


async def edit_reward(session, level, reward_type, reward_value):
    season = await get_active_season(session)
    if not season:
        raise ValueError('Активного сезонного пропуска нет.')
    level = int(level)
    if level not in REWARD_LEVELS:
        raise ValueError('На этом уровне награда не предусмотрена.')
    reward_type = str(reward_type).lower()
    if reward_type == 'money':
        amount = int(reward_value)
        if amount < 0:
            raise ValueError('Сумма не может быть отрицательной.')
        reward = _money(amount)
    elif reward_type in {'medkit', 'morale_boost', 'stamina_boost'}:
        reward = _item(reward_type.upper())
    elif reward_type == 'player':
        pid = int(reward_value)
        row = (await session.execute(text("""
            SELECT id,first_name,last_name,position,pace,shooting,passing,dribbling,defending,physical,stamina,mental,rarity
            FROM players WHERE id=:id AND is_template=TRUE
        """), {'id': pid})).mappings().first()
        if not row:
            raise ValueError('Игрок-шаблон с таким ID не найден.')
        ovr = _player_ovr(row)
        reward = {
            'type': 'player', 'player_id': pid, 'rarity': row['rarity'], 'ovr': ovr,
            'name': display_player_name(row['first_name'], row['last_name']),
            'position': row['position'], 'label': f'{display_player_name(row["first_name"], row["last_name"])} · {row["position"]} · OVR {ovr}',
        }
    else:
        raise ValueError('Неизвестный тип награды.')
    await session.execute(text("""
        INSERT INTO season_pass_rewards(season_pass_id,level,reward_type,reward_value)
        VALUES(:s,:l,:t,CAST(:v AS jsonb))
        ON CONFLICT(season_pass_id,level) DO UPDATE SET reward_type=EXCLUDED.reward_type,reward_value=EXCLUDED.reward_value
    """), {'s': int(season['id']), 'l': level, 't': reward['type'], 'v': json.dumps(reward, ensure_ascii=False)})
    return reward
