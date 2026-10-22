from __future__ import annotations

from sqlalchemy import text

ITEM_LABELS = {
    'MEDKIT': 'Аптечка',
    'MORALE_BOOST': 'Усилитель морали',
    'STAMINA_BOOST': 'Усилитель выносливости',
    'FITNESS_BOOST': 'Усилитель выносливости',
}
USABLE_ITEMS = {'MEDKIT', 'MORALE_BOOST', 'STAMINA_BOOST', 'FITNESS_BOOST'}


def item_label(item_type: str) -> str:
    return ITEM_LABELS.get(str(item_type).upper(), str(item_type).replace('_', ' ').title())


async def add_inventory_item(session, club_id: int, item_type: str, quantity: int = 1):
    item_type = str(item_type).upper()
    quantity = int(quantity)
    if quantity < 1:
        raise ValueError('Количество предметов должно быть положительным.')
    await session.execute(text('''
        INSERT INTO club_inventory(club_id,item_type,quantity)
        VALUES(:c,:item,:qty)
        ON CONFLICT(club_id,item_type) DO UPDATE
        SET quantity=club_inventory.quantity+EXCLUDED.quantity
    '''), {'c': int(club_id), 'item': item_type, 'qty': quantity})


async def list_inventory(session, club_id: int):
    return (await session.execute(text('''
        SELECT item_type,quantity FROM club_inventory
        WHERE club_id=:c AND quantity>0 ORDER BY item_type
    '''), {'c': int(club_id)})).mappings().all()


async def use_inventory_item(session, club_id: int, player_id: int, item_type: str):
    item_type = str(item_type).upper()
    if item_type not in USABLE_ITEMS:
        raise ValueError('Этот предмет нельзя использовать на игроке.')
    canonical_type = 'STAMINA_BOOST' if item_type == 'FITNESS_BOOST' else item_type

    player = (await session.execute(text('''
        SELECT player_id,is_injured,injury_matches,fitness,morale,
               COALESCE(morale_boost_matches,0) AS morale_boost_matches,
               COALESCE(morale_boost_amount,0) AS morale_boost_amount
        FROM club_players WHERE club_id=:c AND player_id=:p FOR UPDATE
    '''), {'c': int(club_id), 'p': int(player_id)})).mappings().first()
    if not player:
        raise ValueError('Игрок не найден в вашем клубе.')

    inventory = (await session.execute(text('''
        SELECT item_type,quantity FROM club_inventory
        WHERE club_id=:c AND item_type IN (:item,:legacy) AND quantity>0
        ORDER BY CASE WHEN item_type=:item THEN 0 ELSE 1 END
        LIMIT 1 FOR UPDATE
    '''), {'c': int(club_id), 'item': canonical_type,
           'legacy': 'FITNESS_BOOST' if canonical_type == 'STAMINA_BOOST' else canonical_type})).mappings().first()
    if not inventory or int(inventory['quantity']) < 1:
        raise ValueError('Этого предмета нет в инвентаре.')

    if canonical_type == 'MEDKIT':
        if not player['is_injured']:
            raise ValueError('У этого игрока нет травмы.')
        await session.execute(text('''
            UPDATE club_players SET is_injured=FALSE,injury_matches=0
            WHERE club_id=:c AND player_id=:p
        '''), {'c': int(club_id), 'p': int(player_id)})
        message = '🩹 Аптечка использована: травма вылечена.'
    elif canonical_type == 'MORALE_BOOST':
        await session.execute(text('''
            UPDATE club_players
            SET morale=LEAST(100,GREATEST(0,morale-COALESCE(morale_boost_amount,0))+30),
                morale_boost_amount=LEAST(30,100-GREATEST(0,morale-COALESCE(morale_boost_amount,0))),
                morale_boost_matches=3
            WHERE club_id=:c AND player_id=:p
        '''), {'c': int(club_id), 'p': int(player_id)})
        message = '😊 Усилитель морали активирован на 3 матча (+30 к морали).'
    else:  # STAMINA_BOOST: restore up to 30 fitness points.
        await session.execute(text('''
            UPDATE club_players SET fitness=LEAST(100,fitness+30)
            WHERE club_id=:c AND player_id=:p
        '''), {'c': int(club_id), 'p': int(player_id)})
        message = '💪 Выносливость повышена на 30 пунктов (до 100).'

    await session.execute(text('''
        UPDATE club_inventory SET quantity=quantity-1
        WHERE club_id=:c AND item_type=:item
    '''), {'c': int(club_id), 'item': str(inventory['item_type'])})
    await session.execute(text('DELETE FROM club_inventory WHERE club_id=:c AND quantity<=0'), {'c': int(club_id)})
    return message


async def team_item_status(session, club_id: int, item_type: str) -> dict:
    # Return eligible player IDs and the canonical/legacy item count.
    item_type = str(item_type).upper()
    canonical = 'STAMINA_BOOST' if item_type == 'FITNESS_BOOST' else item_type
    if canonical not in {'MEDKIT', 'MORALE_BOOST', 'STAMINA_BOOST'}:
        raise ValueError('Этот предмет нельзя применить ко всей команде.')
    if canonical == 'MEDKIT':
        condition = 'is_injured IS TRUE'
    elif canonical == 'STAMINA_BOOST':
        condition = 'fitness < 100'
    else:
        condition = 'COALESCE(morale,70) < 100'
    players = (await session.execute(text(
        f'SELECT player_id FROM club_players WHERE club_id=:c AND {condition} ORDER BY player_id'
    ), {'c': int(club_id)})).scalars().all()
    inv_types = (canonical, 'FITNESS_BOOST') if canonical == 'STAMINA_BOOST' else (canonical,)
    available = int((await session.execute(text('''
        SELECT COALESCE(SUM(quantity),0) FROM club_inventory
        WHERE club_id=:c AND item_type=ANY(:items) AND quantity>0
    '''), {'c': int(club_id), 'items': list(inv_types)})).scalar_one())
    return {'player_ids': [int(x) for x in players], 'needed': len(players), 'available': available}


async def use_inventory_item_for_team(session, club_id: int, item_type: str) -> dict:
    # Apply one item per eligible player, up to the available inventory quantity.
    item_type = str(item_type).upper()
    canonical = 'STAMINA_BOOST' if item_type == 'FITNESS_BOOST' else item_type
    if canonical not in {'MEDKIT', 'MORALE_BOOST', 'STAMINA_BOOST'}:
        raise ValueError('Этот предмет нельзя применить ко всей команде.')
    status = await team_item_status(session, club_id, canonical)
    if status['needed'] == 0:
        raise ValueError('Нет игроков, которым нужен этот предмет.')
    if status['available'] == 0:
        raise ValueError('Этого предмета нет в инвентаре.')
    inv_types = (canonical, 'FITNESS_BOOST') if canonical == 'STAMINA_BOOST' else (canonical,)
    await session.execute(text('''
        SELECT item_type,quantity FROM club_inventory
        WHERE club_id=:c AND item_type=ANY(:items) AND quantity>0
        ORDER BY item_type FOR UPDATE
    '''), {'c': int(club_id), 'items': list(inv_types)})
    status = await team_item_status(session, club_id, canonical)
    apply_count = min(status['needed'], status['available'])
    applied = 0
    for player_id in status['player_ids'][:apply_count]:
        await use_inventory_item(session, int(club_id), player_id, canonical)
        applied += 1
    return {'item_type': canonical, 'needed': status['needed'],
            'available': status['available'], 'applied': applied}


async def advance_morale_boosts(session, club_ids):
    club_ids = sorted({int(cid) for cid in club_ids if cid is not None})
    if not club_ids:
        return
    await session.execute(text('''
        UPDATE club_players
        SET morale=CASE WHEN morale_boost_matches<=1
                        THEN GREATEST(0,morale-COALESCE(morale_boost_amount,0))
                        ELSE morale END,
            morale_boost_matches=GREATEST(0,morale_boost_matches-1),
            morale_boost_amount=CASE WHEN morale_boost_matches<=1 THEN 0 ELSE morale_boost_amount END
        WHERE club_id=ANY(:clubs) AND COALESCE(morale_boost_matches,0)>0
    '''), {'clubs': club_ids})
