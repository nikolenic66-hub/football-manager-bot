from app.game.rng import rng
from types import SimpleNamespace

from sqlalchemy import text

from app.game.ratings import player_rating
from app.services import add_copy_to_club, display_player_name, record_financial_transaction
from app.game.liveops import get_liveops_config, multiplier


async def list_packs(session):
    return (await session.execute(text("""
        SELECT id,name,price_coins,price_stars,min_ovr,max_ovr
        FROM packs ORDER BY id
    """))).mappings().all()


def _template_ovr(row):
    player = SimpleNamespace(
        id=int(row['id']), position=str(row['position']),
        pace=float(row['pace'] or 0), shooting=float(row['shooting'] or 0),
        passing=float(row['passing'] or 0), dribbling=float(row['dribbling'] or 0),
        defending=float(row['defending'] or 0), physical=float(row['physical'] or 0),
        stamina=float(row['stamina'] or 0), mental=float(row['mental'] or 0),
        fitness=100, form=0, morale=70,
    )
    return int(round(float(player_rating(player))))


async def open_pack(session, club_id, pack_id):
    pack = (await session.execute(text("""
        SELECT id,name,price_coins,price_stars,min_ovr,max_ovr
        FROM packs WHERE id=:p FOR UPDATE
    """), {'p': int(pack_id)})).mappings().first()
    if not pack:
        raise ValueError('Пак не найден.')

    # Lock the club through the financial transaction. The transaction helper
    # performs the budget check and records the debit atomically.
    candidates = (await session.execute(text("""
        SELECT id,first_name,last_name,nationality,age,position,pace,shooting,
               passing,dribbling,defending,physical,stamina,mental,market_value,
               salary,potential,rarity,card_version,real_player,preferred_foot,portrait_url,
               secondary_positions,talents,tactical_archetype
        FROM players
        WHERE is_template=TRUE
        ORDER BY id
    """))).mappings().all()
    matching = [r for r in candidates if int(pack['min_ovr']) <= _template_ovr(r) <= int(pack['max_ovr'])]
    if not matching:
        raise ValueError('В этом паке сейчас нет подходящих игроков.')

    season_row = (await session.execute(text("SELECT id,season_seed FROM seasons WHERE state='ACTIVE' ORDER BY id DESC LIMIT 1"))).mappings().first()
    root_seed = int(season_row['season_seed']) if season_row else int(pack['id'])
    rng(root_seed, 'pack_open', int(club_id), int(pack_id)).shuffle(matching)
    template = None
    for row in matching:
        owned = await session.execute(text("""
            SELECT 1 FROM club_players
            WHERE club_id=:c AND template_id=:t LIMIT 1
        """), {'c': int(club_id), 't': int(row['id'])})
        if owned.first() is None:
            template = row
            break
    if template is None:
        raise ValueError('Для этого пака не осталось свободных карточек в базе.')

    cfg = await get_liveops_config(session, int(season_row['id'])) if season_row else {}
    price = int(round(int(pack['price_coins']) * multiplier(cfg, 'pack_price_multiplier')))
    await record_financial_transaction(
        session, int(club_id), 'OTHER', -price,
        f'Открытие пака «{pack["name"]}»'
    )
    player_id, _ = await add_copy_to_club(session, int(club_id), int(template['id']))
    if player_id is None:
        raise ValueError('Не удалось выдать игрока клубу.')

    await session.execute(text("""
        INSERT INTO pack_history(club_id,pack_id,player_id,created_at)
        VALUES(:c,:p,:player,now())
    """), {'c': int(club_id), 'p': int(pack['id']), 'player': int(player_id)})

    return {
        'pack_id': int(pack['id']), 'pack_name': pack['name'],
        'player_id': int(player_id), 'template_id': int(template['id']),
        'name': display_player_name(template['first_name'], template['last_name']),
        'position': template['position'], 'ovr': _template_ovr(template),
        'rarity': str(template['rarity'] or 'BASE').upper(), 'price_coins': price,
        'age': int(template['age'] or 18), 'nationality': template['nationality'],
        'potential': int(template['potential'] or _template_ovr(template)),
        'portrait_url': template['portrait_url'], 'card_version': template['card_version'],
    }


async def send_stars_invoice(*args, **kwargs):
    """Telegram Stars invoice integration — в разработке."""
    return None
