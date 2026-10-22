"""Club reputation progression for match and season results."""
from __future__ import annotations
from sqlalchemy import text


def clamp_reputation(value):
    return round(max(0.0, min(100.0, float(value))), 2)


async def apply_match_reputation(session, home_club_id, away_club_id, home_score, away_score):
    if home_score == away_score:
        deltas = ((home_club_id, 0.0), (away_club_id, 0.0))
    elif home_score > away_score:
        deltas = ((home_club_id, 0.1), (away_club_id, -0.1))
    else:
        deltas = ((home_club_id, -0.1), (away_club_id, 0.1))
    for club_id, delta in deltas:
        row = (await session.execute(text("SELECT name,reputation FROM clubs WHERE id=:c FOR UPDATE"), {'c': club_id})).mappings().first()
        if not row:
            continue
        new_value = clamp_reputation(float(row['reputation']) + delta)
        await session.execute(text("UPDATE clubs SET reputation=:r WHERE id=:c"), {'r': new_value, 'c': club_id})
        import logging
        logging.getLogger(__name__).info('Репутация клуба %s %s до %.2f', row['name'], 'выросла' if delta > 0 else 'снизилась', new_value)


async def apply_season_reputation(session, league_id):
    rows = (await session.execute(text("""SELECT club_id,name,reputation,
        ROW_NUMBER() OVER (ORDER BY points DESC,(goals_for-goals_against) DESC,goals_for DESC,club_id) position,
        COUNT(*) OVER () teams
        FROM league_teams JOIN clubs ON clubs.id = league_teams.club_id WHERE league_id=:l"""), {'l': league_id})).mappings().all()
    for row in rows:
        position = int(row['position']); teams = int(row['teams'])
        if position == 1:
            delta = 5.0
        elif position in (2, 3):
            delta = 3.0
        elif position == teams:
            delta = -5.0
        else:
            delta = 0.0
        if delta:
            new_value = clamp_reputation(float(row['reputation']) + delta)
            await session.execute(text("UPDATE clubs SET reputation=:r WHERE id=:c"), {'r': new_value, 'c': row['club_id']})
            import logging
            logging.getLogger(__name__).info('Репутация клуба %s %s до %.2f', row['name'], 'выросла' if delta > 0 else 'снизилась', new_value)
