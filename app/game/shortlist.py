"""Club player shortlist."""
from sqlalchemy import text


async def add_to_shortlist(club_id, player_id, session):
    row = (await session.execute(text('''
        INSERT INTO shortlist(club_id,player_id,created_at)
        VALUES(:club_id,:player_id,CAST(:created_at AS timestamptz))
        ON CONFLICT(club_id,player_id) DO NOTHING
        RETURNING id,club_id,player_id,created_at
    '''), {
        'club_id': int(club_id), 'player_id': int(player_id),
        'created_at': __import__('datetime').datetime.now(__import__('datetime').timezone.utc),
    })).mappings().first()
    if row:
        return dict(row)
    return (await session.execute(text('''
        SELECT id,club_id,player_id,created_at FROM shortlist
        WHERE club_id=:club_id AND player_id=:player_id
    '''), {'club_id': int(club_id), 'player_id': int(player_id)})).mappings().first()


async def remove_from_shortlist(club_id, player_id, session):
    result = await session.execute(text('''
        DELETE FROM shortlist WHERE club_id=:club_id AND player_id=:player_id
    '''), {'club_id': int(club_id), 'player_id': int(player_id)})
    return result.rowcount > 0


async def get_shortlist(club_id, session):
    rows = (await session.execute(text('''
        SELECT s.id,s.club_id,s.player_id,s.created_at,
               p.first_name,p.last_name,p.position,p.age,p.potential,p.market_value,p.salary,
               p.pace,p.shooting,p.passing,p.dribbling,p.defending,p.physical,p.stamina,p.mental
        FROM shortlist s JOIN players p ON p.id=s.player_id
        WHERE s.club_id=:club_id ORDER BY s.created_at DESC,s.id DESC
    '''), {'club_id': int(club_id)})).mappings().all()
    return [dict(row) for row in rows]
