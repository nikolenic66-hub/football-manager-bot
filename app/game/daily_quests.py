from __future__ import annotations

from datetime import date
import json
from sqlalchemy import text


QUESTS = (
    {'quest_type': 'PLAY_MATCH', 'title': 'Сыграй матч', 'target': 1, 'reward': {'type': 'bp_xp', 'amount': 10, 'label': '10 BP XP'}},
    {'quest_type': 'SCORE_GOALS', 'title': 'Забей 3 гола', 'target': 3, 'reward': {'type': 'money', 'amount': 25000, 'label': '€25,000'}},
    {'quest_type': 'CHANGE_TACTICS', 'title': 'Поменяй тактику', 'target': 1, 'reward': {'type': 'item', 'item': 'MEDKIT', 'qty': 1, 'label': '1 MEDKIT'}},
)


async def ensure_daily_quests(session, club_id, today: date | None = None):
    day = today or date.today()
    configured = (await session.execute(text("SELECT quest_type,title,target,reward FROM liveops_daily_quest_definitions WHERE enabled=TRUE ORDER BY sort_order,id"))).mappings().all()
    quest_specs = tuple(dict(r) for r in configured) or QUESTS
    existing = int((await session.execute(text(
        'SELECT count(*) FROM daily_quests WHERE club_id=:c AND quest_date=:d'), {'c': int(club_id), 'd': day})).scalar_one())
    if existing == len(quest_specs):
        return await list_daily_quests(session, club_id, day)
    for spec in quest_specs:
        await session.execute(text('''
            INSERT INTO daily_quests(club_id,quest_date,quest_type,progress,target,reward,status)
            VALUES(:c,:d,:t,0,:target,CAST(:reward AS jsonb),'ACTIVE')
            ON CONFLICT(club_id,quest_date,quest_type) DO NOTHING
        '''), {'c': int(club_id), 'd': day, 't': spec['quest_type'], 'target': int(spec['target']),
               'reward': json.dumps(spec['reward'], ensure_ascii=False)})
    return await list_daily_quests(session, club_id, day)


async def list_daily_quests(session, club_id, today: date | None = None):
    day = today or date.today()
    return (await session.execute(text('''
        SELECT id,club_id,quest_date,quest_type,progress,target,reward,status
        FROM daily_quests WHERE club_id=:c AND quest_date=:d ORDER BY id
    '''), {'c': int(club_id), 'd': day})).mappings().all()


async def _progress(session, club_id, quest_type, amount=1):
    rows = (await session.execute(text('''
        SELECT id,progress,target,status,reward FROM daily_quests
        WHERE club_id=:c AND quest_date=CURRENT_DATE AND quest_type=:t
        FOR UPDATE
    '''), {'c': int(club_id), 't': str(quest_type)})).mappings().all()
    if not rows:
        await ensure_daily_quests(session, club_id)
        rows = (await session.execute(text('''SELECT id,progress,target,status,reward FROM daily_quests
            WHERE club_id=:c AND quest_date=CURRENT_DATE AND quest_type=:t FOR UPDATE'''),
            {'c': int(club_id), 't': str(quest_type)})).mappings().all()
    if not rows:
        return False
    row = rows[0]
    if row['status'] != 'ACTIVE':
        return False
    progress = min(int(row['target']), int(row['progress']) + int(amount))
    status = 'COMPLETED' if progress >= int(row['target']) else 'ACTIVE'
    await session.execute(text('UPDATE daily_quests SET progress=:p,status=:s WHERE id=:id'),
                          {'p': progress, 's': status, 'id': int(row['id'])})
    if status == 'COMPLETED':
        # BP XP is granted on completion; the other reward types are claimed explicitly.
        from app.game.battle_pass import add_battle_pass_points
        reward = dict(row['reward'] or {})
        rarity = str(reward.get('rarity') or '').lower()
        bp_bonus = 25 if rarity == 'rare' else 15
        # Keep any existing BP-XP quest reward, then add the new completion bonus.
        existing_bp_reward = int(reward.get('amount') or 0) if reward.get('type') == 'bp_xp' else 0
        await add_battle_pass_points(int(club_id), existing_bp_reward + bp_bonus, session)
        return True
    return False


async def record_match(session, club_id, goals=0):
    await _progress(session, club_id, 'PLAY_MATCH', 1)
    if int(goals) > 0:
        await _progress(session, club_id, 'SCORE_GOALS', int(goals))


async def record_tactic_change(session, club_id):
    await _progress(session, club_id, 'CHANGE_TACTICS', 1)


async def claim_quest(session, club_id, quest_id):
    row = (await session.execute(text('''
        SELECT * FROM daily_quests WHERE id=:id AND club_id=:c FOR UPDATE
    '''), {'id': int(quest_id), 'c': int(club_id)})).mappings().first()
    if not row or row['status'] != 'COMPLETED':
        raise ValueError('Задание ещё не выполнено или уже забрано.')
    reward = dict(row['reward'] or {})
    rtype = reward.get('type')
    if rtype == 'money':
        from app.services import record_financial_transaction
        await record_financial_transaction(session, int(club_id), 'OTHER', int(reward['amount']), 'Награда ежедневного задания')
    elif rtype == 'item':
        await session.execute(text('''
            INSERT INTO club_inventory(club_id,item_type,quantity)
            VALUES(:c,:item,:qty)
            ON CONFLICT(club_id,item_type) DO UPDATE SET quantity=club_inventory.quantity+EXCLUDED.quantity
        '''), {'c': int(club_id), 'item': str(reward['item']), 'qty': int(reward.get('qty', 1))})
    await session.execute(text("UPDATE daily_quests SET status='CLAIMED' WHERE id=:id"), {'id': int(quest_id)})
    return reward
