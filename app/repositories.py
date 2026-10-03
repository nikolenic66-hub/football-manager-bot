from .visual import club_crest
from sqlalchemy import text


async def get_or_create_user(s, telegram_id, username, first_name):
    row = (await s.execute(text('''
      INSERT INTO users(telegram_id,username,first_name) VALUES(:t,:u,:f)
      ON CONFLICT(telegram_id) DO UPDATE SET username=EXCLUDED.username,first_name=EXCLUDED.first_name
      RETURNING id
    '''), {'t': telegram_id, 'u': username, 'f': first_name})).first()
    await s.execute(text('INSERT INTO notification_settings(user_id) VALUES(:u) ON CONFLICT DO NOTHING'), {'u': row.id})
    return row.id


async def create_club(s, owner_id, name):
    row = (await s.execute(text('''
      INSERT INTO clubs(owner_user_id,name,short_name,stadium_name,budget,fans,reputation)
      VALUES(:o,:n,:sn,:st,20000000,5000,1) RETURNING id
    '''), {'o': owner_id, 'n': name, 'sn': name[:4].upper(), 'st': name + ' Arena'})).first()
    cid = row.id
    try:
        club_crest(name, cid)
    except Exception:
        pass

    positions = ['GK', 'GK', 'GK', 'CB', 'CB', 'CB', 'CB', 'LB', 'RB', 'DM', 'DM', 'CM', 'CM', 'CM', 'AM', 'LW', 'RW', 'ST', 'ST', 'ST', 'ST', 'CM', 'LB']
    for number, pos in enumerate(positions, 1):
        p = (await s.execute(text('''
          SELECT id FROM players p
          WHERE p.position=:pos
            AND NOT EXISTS(SELECT 1 FROM club_players cp WHERE cp.player_id=p.id)
          ORDER BY p.potential DESC, p.id
          LIMIT 1
        '''), {'pos': pos})).first()
        if p:
            await s.execute(text('''
                INSERT INTO club_players(club_id,player_id,shirt_number,contract_salary,release_clause,contract_until)
                VALUES(:c,:p,:n,:sal,:clause,:until)
            '''), {'c': cid, 'p': p.id, 'n': number, 'sal': 0, 'clause': 0, 'until': None})

    await s.execute(text('''
        UPDATE club_players cp
        SET contract_salary = p.salary,
            release_clause = GREATEST(p.market_value * 2, p.salary * 36),
            contract_until = CURRENT_DATE + INTERVAL '3 years'
        FROM players p
        WHERE cp.club_id = :c
          AND p.id = cp.player_id
    '''), {'c': cid})

    await s.execute(text('''
        INSERT INTO club_training(club_id) VALUES(:c)
        ON CONFLICT (club_id) DO NOTHING
    '''), {'c': cid})
    await s.execute(text('''
        INSERT INTO club_tactics(club_id) VALUES(:c)
        ON CONFLICT (club_id) DO NOTHING
    '''), {'c': cid})
    return cid
