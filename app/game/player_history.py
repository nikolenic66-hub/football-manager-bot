"""Player development snapshots."""
from datetime import datetime, timedelta, timezone
from sqlalchemy import text
from app.game.ratings import player_rating


def _overall(row):
    attrs={k:int(row.get(k) or 0) for k in ('pace','shooting','passing','dribbling','defending','physical','stamina','mental')}
    player=type('HistoryPlayer',(),attrs)()
    player.position=str(row.get('position') or 'CM'); player.fitness=int(row.get('fitness') or 100); player.form=0; player.morale=int(row.get('morale') or 70)
    return int(round(float(player_rating(player))))


async def record_snapshot(player_id, session, note=None):
    row=(await session.execute(text('''SELECT p.id,p.position,p.potential,p.market_value,p.pace,p.shooting,p.passing,
        p.dribbling,p.defending,p.physical,p.stamina,p.mental,cp.morale,cp.fitness
        FROM players p JOIN club_players cp ON cp.player_id=p.id
        WHERE p.id=:p ORDER BY cp.club_id LIMIT 1'''), {'p': int(player_id)})).mappings().first()
    if not row:
        raise ValueError('Player is not owned by a club.')
    await session.execute(text('''INSERT INTO player_history
        (player_id,recorded_at,ovr,potential,market_value,morale,fitness,note)
        VALUES(:p,CAST(:recorded_at AS timestamptz),:ovr,:potential,:value,:morale,:fitness,:note)'''), {
        'p': int(player_id), 'recorded_at': __import__('datetime').datetime.now(__import__('datetime').timezone.utc),
        'ovr': _overall(row), 'potential': int(row['potential']), 'value': int(row['market_value']),
        'morale': int(row['morale']), 'fitness': int(row['fitness']), 'note': note,
    })
    return {'player_id': int(player_id), 'ovr': _overall(row), 'potential': int(row['potential']),
            'market_value': int(row['market_value']), 'morale': int(row['morale']), 'fitness': int(row['fitness']), 'note': note}


async def record_snapshots(player_ids, session, note=None):
    ids=[int(pid) for pid in player_ids]
    if not ids: return []
    rows=(await session.execute(text("""SELECT p.id,p.position,p.potential,p.market_value,p.pace,p.shooting,p.passing,p.dribbling,p.defending,p.physical,p.stamina,p.mental,cp.morale,cp.fitness
        FROM players p JOIN club_players cp ON cp.player_id=p.id WHERE p.id=ANY(:ids) ORDER BY cp.club_id,p.id"""),{'ids':ids})).mappings().all()
    now=datetime.now(timezone.utc); values=[]; out=[]; seen=set()
    for row in rows:
        pid=int(row['id'])
        if pid in seen: continue
        seen.add(pid); ovr=_overall(row)
        out.append({'player_id':pid,'ovr':ovr,'potential':int(row['potential']),'market_value':int(row['market_value']),'morale':int(row['morale']),'fitness':int(row['fitness']),'note':note})
        values.append({'p':pid,'recorded_at':now,'ovr':ovr,'potential':int(row['potential']),'value':int(row['market_value']),'morale':int(row['morale']),'fitness':int(row['fitness']),'note':note})
    if len(seen)!=len(ids): raise ValueError('Player is not owned by a club.')
    await session.execute(text("""INSERT INTO player_history(player_id,recorded_at,ovr,potential,market_value,morale,fitness,note)
        VALUES(:p,CAST(:recorded_at AS timestamptz),:ovr,:potential,:value,:morale,:fitness,:note)"""),values)
    return out


async def get_history(player_id, session):
    rows=(await session.execute(text('''SELECT player_id,recorded_at,ovr,potential,market_value,morale,fitness,note
        FROM player_history WHERE player_id=:p ORDER BY recorded_at ASC,id ASC'''), {'p': int(player_id)})).mappings().all()
    return [dict(r) for r in rows]


def format_history(history):
    if not history:
        return ''
    latest_by_year={}
    for row in history:
        ts=row.get('recorded_at')
        year=getattr(ts, 'year', None) or str(ts)[:4]
        latest_by_year[year]=int(row['ovr'])
    return ' → '.join(f'{year}: {ovr}' for year,ovr in latest_by_year.items())


async def get_development_trend(player_id, session, days=30):
    """Return (date, ovr, market_value, morale) snapshots for the period."""
    days = max(1, int(days))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (await session.execute(text("""
        SELECT recorded_at,ovr,market_value,morale
        FROM (
            SELECT DISTINCT ON ((recorded_at AT TIME ZONE 'UTC')::date)
                recorded_at,ovr,market_value,morale
            FROM player_history
            WHERE player_id=:p
              AND recorded_at >= CAST(:cutoff AS timestamptz)
            ORDER BY (recorded_at AT TIME ZONE 'UTC')::date ASC, recorded_at DESC, id DESC
        ) daily
        ORDER BY recorded_at ASC
    """), {'p': int(player_id), 'cutoff': cutoff})).mappings().all()
    return [(row['recorded_at'], int(row['ovr']), int(row['market_value']), int(row['morale'])) for row in rows]


def format_trend(trend):
    if not trend:
        return ''
    points=[]; seen_dates=set()
    for recorded_at,ovr,value,morale in trend:
        date=recorded_at.date() if hasattr(recorded_at,'date') else str(recorded_at)[:10]
        if date in seen_dates:
            continue
        seen_dates.add(date); points.append((date,int(ovr),int(value),int(morale)))
    if not points:
        return ''
    if len(points)<=3:
        return ' → '.join(f'{date}: {ovr}' for date,ovr,_value,_morale in points)
    visible=points[-8:]; values=[p[1] for p in visible]; lo=min(values); hi=max(values)
    levels='▁▂▃▄▅▆▇█'
    if hi==lo:
        graph=levels[0]*len(visible)
    else:
        graph=''.join(levels[max(0,min(len(levels)-1,round((ovr-lo)/(hi-lo)*(len(levels)-1))))] for _,ovr,_,_ in visible)
    return f'{visible[0][1]} {graph} {visible[-1][1]}'

