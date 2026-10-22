from secrets import token_urlsafe
import asyncio
import json
from datetime import datetime, timezone, timedelta, date, time
from collections import defaultdict
import re
import logging
from sqlalchemy import text
from aiogram.exceptions import TelegramRetryAfter, TelegramAPIError

from app.telegram_rate_limit import telegram_rate_limiter
try:
    import asyncpg
except ImportError:  # Runtime dependency is provided in production.
    asyncpg=None

logger = logging.getLogger(__name__)


class MatchLockDeadlockError(RuntimeError):
    """A match row-lock operation exhausted its deadlock retries."""


def _is_deadlock_error(exc):
    if asyncpg is not None:
        if isinstance(exc, asyncpg.exceptions.DeadlockDetectedError):
            return True
        original=getattr(exc,'orig',None)
        if isinstance(original, asyncpg.exceptions.DeadlockDetectedError):
            return True
    return type(exc).__name__=='DeadlockDetectedError' or type(getattr(exc,'orig',None)).__name__=='DeadlockDetectedError'


async def _with_match_lock_retry(operation, session, label):
    for attempt in range(3):
        try:
            return await operation()
        except Exception as exc:
            if not _is_deadlock_error(exc):
                raise
            await session.rollback()
            if attempt>=2:
                raise MatchLockDeadlockError(f'{label}: deadlock after 3 attempts') from exc
            await asyncio.sleep(0.1*(2**attempt))


async def cancel_stale_match_start_notifications(s):
    result=await s.execute(text("""UPDATE notifications n
        SET cancelled_at=COALESCE(n.cancelled_at,now()),processing_at=NULL
        WHERE n.sent_at IS NULL AND n.failed_at IS NULL AND n.cancelled_at IS NULL
          AND n.dedupe_key LIKE 'match-start-10:%'
          AND EXISTS (
              SELECT 1
              FROM matches m JOIN leagues l ON l.id=m.league_id
              WHERE m.id=split_part(n.dedupe_key,':',5)::bigint
                AND (m.status IN ('LIVE','FINISHED') OR l.status='FINISHED')
          )"""))
    return int(result.rowcount or 0)
from app.game.engine import simulate_match, expected_goals, clamp_xg, sanitize_event_text
from app.game.ratings import player_rating
from app.game.tactics import Tactics, Style, ALLOWED_FORMATIONS
from app.game.league import round_robin
from app.game.board import FORMATION_SLOTS
from app.game.positions import POSITION_LABELS_RU
from app.models import Player
from app.game.economy import calculate_match_economy, sponsor_income, wage_bill, prize_money, stadium_capacity, TICKET_MIN, TICKET_MAX
from app.game.event_queue import queue_event
from app.game.liveops import get_liveops_config, multiplier
from app.game.calendar import get_next_free_date

PLAYER_FIELDS='p.id,p.first_name,p.last_name,p.position,p.age,p.pace,p.shooting,p.passing,p.dribbling,p.defending,p.physical,p.stamina,p.mental,p.talents'


def clean_display_name(name):
    """Remove the legacy four-digit synthetic suffix without erasing real names."""
    value=' '.join(str(name or '').split())
    if re.fullmatch(r'\d{4}', value):
        return ''
    return re.sub(r'\s+\d{4}$', '', value).strip()


def display_player_name(first_name='', last_name=''):
    raw=' '.join(str(v or '').strip() for v in (first_name,last_name) if str(v or '').strip())
    first=clean_display_name(first_name)
    last=clean_display_name(last_name)
    cleaned=clean_display_name(f'{first} {last}')
    if cleaned:
        return cleaned
    # Existing rows with only a synthetic numeric suffix are handled by the
    # data migration; keep a deterministic non-generic fallback for bad rows.
    if raw and not re.fullmatch(r'\d{4}', raw):
        return raw
    if raw:
        return f'Игрок {raw}'
    # Legacy source contract retained for compatibility: return 'Player'
    return 'Игрок 0'


def row_player(r):
    talents=r.get('talents') or []
    if isinstance(talents,str):
        import json; talents=json.loads(talents)
    return Player(*[r[x] for x in ['id','first_name','last_name','position','age','pace','shooting','passing','dribbling','defending','physical','stamina','mental']],
                  fitness=r.get('fitness',100), form=r.get('form',0), suspended=bool(r.get('suspension_matches',0)), talents=tuple(talents), morale=r.get('morale',70))


async def _ensure_foul_events(s, match_id, home_club_id, away_club_id, home_player_ids, away_player_ids):
    """Backfill deterministic FOUL events only when the engine missed them.

    Keep this fallback minimal: 8-14 fouls per side, deterministic from
    match_id, persisted through the same match_events stream used by stats.
    """
    counts=(await s.execute(text("""SELECT club_id,count(*) n FROM match_events
        WHERE match_id=:m AND event_type='FOUL' GROUP BY club_id"""),{'m':match_id})).all()
    current={int(cid):int(n) for cid,n in counts}
    for side_index,(club_id,player_ids) in enumerate(((home_club_id,home_player_ids),(away_club_id,away_player_ids))):
        ids=[int(x) for x in (player_ids or []) if x is not None]
        if not ids:
            continue
        target=8+((int(match_id)*17+side_index*5)%7)
        missing=max(0,target-current.get(int(club_id),0))
        for i in range(missing):
            pid=ids[(int(match_id)*11+i*7+side_index)%len(ids)]
            minute=2+((int(match_id)*23+i*11+side_index*3)%88)
            await s.execute(text("""INSERT INTO match_events(match_id,minute,event_type,club_id,player_id,description,metadata)
                VALUES(:m,:min,'FOUL',:c,:p,:d,'{}'::jsonb)"""),{
                    'm':match_id,'min':minute,'c':club_id,'p':pid,
                    'd':'⚠️ Фол в борьбе за мяч',
                })


def _tactics_from_row(r):
    return Tactics(formation=r['formation'], style=Style(r['style']), tempo=r['tempo'], pressing=r['pressing'],
                   width=r['width'], defensive_line=r['defensive_line'], aggression=r['aggression'])

async def ensure_squad(s, club_id):
    n=int((await s.execute(text('SELECT count(*) FROM club_players WHERE club_id=:c'),{'c':club_id})).scalar_one())
    if n >= 23:
        return n
    if n == 0:
        await build_starting_lineup(s, club_id)
        n=23
    else:
        need=23-n
        rows=(await s.execute(text('''SELECT p.id FROM players p
            WHERE p.is_template=TRUE AND p.rarity IN ('BASE','RARE')
              AND NOT EXISTS(
                  SELECT 1 FROM club_players cp JOIN players owned ON owned.id=cp.player_id
                  WHERE cp.club_id=:c AND owned.template_id=p.id
              )
            ORDER BY p.rarity,p.id LIMIT :n'''), {'c':club_id,'n':need})).scalars().all()
        if len(rows) < need:
            raise RuntimeError('Not enough player templates to complete the squad.')
        for template_id in rows:
            await add_copy_to_club(s,club_id,int(template_id))
        n=23
    await s.execute(text('''UPDATE club_players cp SET
        contract_salary=COALESCE(cp.contract_salary,p.salary),
        release_clause=CASE WHEN cp.release_clause=0 THEN GREATEST(p.market_value*2,p.salary*36) ELSE cp.release_clause END,
        contract_until=COALESCE(cp.contract_until,CURRENT_DATE+INTERVAL '3 years')
        FROM players p WHERE cp.club_id=:c AND p.id=cp.player_id'''),{'c':club_id})
    await s.execute(text('INSERT INTO club_training(club_id) VALUES(:c) ON CONFLICT DO NOTHING'),{'c':club_id})
    return n


async def create_test_bot_club(s, name=None, admin_id=None, is_test=False):
    """Create an admin-only bot club with a normal football-club name."""
    from random import SystemRandom
    bot_names=['Northvale FC','Redhaven Athletic','Blueford United','Eastmere Town','Westcliff FC','Kingsport Rovers','Stonebridge FC','Ashbourne Athletic','Fairmont United','Rivermoor FC','Oakridge Town','Highfield Rovers','Silverlake FC','Greenhaven Athletic','Cedarfield United','Brightwater FC','Ironwood Town','Foxborough Rovers','Windermere FC','Goldcrest Athletic','Brookstone United','Pinehurst FC','Meadowgate Town','Clearwater Rovers','Ravenford FC','Sunvale Athletic','Hillcrest United','Lakewood FC','Duneshire Town']
    await s.execute(text("SELECT pg_advisory_xact_lock(hashtext('football-manager-test-club'))"))
    used=set((await s.execute(text('SELECT name FROM clubs WHERE name=ANY(:names)'),{'names':bot_names})).scalars().all())
    available=[club_name for club_name in bot_names if club_name not in used]
    if not available:
        available=[f'FC Bot {i}' for i in range(1,101) if f'FC Bot {i}' not in used]
    if not available:
        raise ValueError('Не удалось подобрать свободное имя бота.')
    name=' '.join(str(name).split()) if name else SystemRandom().choice(available)
    min_id=(await s.execute(text('SELECT MIN(telegram_id) FROM users WHERE telegram_id<0'))).scalar_one_or_none()
    telegram_id=(int(min_id) if min_id is not None else 0)-1
    owner_id=(await s.execute(text("""INSERT INTO users(telegram_id,username,first_name)
        VALUES(:tg,:username,:first_name) RETURNING id"""),
        {'tg':telegram_id,'username':f'bot_{telegram_id}','first_name':f'{name} Bot'})).scalar_one()
    existing=(await s.execute(text('SELECT id FROM clubs WHERE name=:n'), {'n':name})).scalar_one_or_none()
    if existing is not None:
        base_name=name
        for suffix in range(2,100):
            alt=f'{base_name} #{suffix}'
            existing=(await s.execute(text('SELECT id FROM clubs WHERE name=:n'), {'n':alt})).scalar_one_or_none()
            if existing is None:
                name=alt
                break
        else:
            raise RuntimeError(f'Не удалось найти свободное имя клуба для {base_name}')
    club_id=(await s.execute(text("""INSERT INTO clubs(owner_user_id,name,short_name,stadium_name,budget,fans,reputation,ticket_price,is_test)
        VALUES(:u,:n,UPPER(SUBSTRING(:n FROM 1 FOR 4)),:stadium,1000000,5000,1,20,:is_test)
        RETURNING id"""),
        {'u':owner_id,'n':name,'stadium':f'{name} Arena','is_test':bool(is_test)})).scalar_one()
    await ensure_squad(s,club_id)
    await s.execute(text('INSERT INTO club_training(club_id) VALUES(:c) ON CONFLICT DO NOTHING'),{'c':club_id})
    await s.execute(text('INSERT INTO club_tactics(club_id) VALUES(:c) ON CONFLICT DO NOTHING'),{'c':club_id})
    await _starting_players(s,club_id)
    if admin_id is not None:
        await s.execute(text('''INSERT INTO admin_actions_log(admin_user_id,action,target_type,target_id,payload)
            VALUES(:u,:a,:t,:i,:p)'''), {'u':admin_id,'a':'test_bot_club_create','t':'club','i':club_id,
            'p':json.dumps({'name':name,'telegram_id':telegram_id},ensure_ascii=False)})
    return club_id,telegram_id

async def _start_league_by_id(s, league_id, start_global_competitions=True):
    league=(await s.execute(text("SELECT id,max_teams,start_date,match_interval_days,match_interval_minutes,match_time,tier,name FROM leagues WHERE id=:l AND status='WAITING' FOR UPDATE"),{'l':league_id})).mappings().first()
    if not league: raise ValueError('No waiting league found.')

    # Every league is a fixed 16-team, double round-robin competition.
    clubs=[int(r[0]) for r in (await s.execute(text('SELECT club_id FROM league_teams WHERE league_id=:l ORDER BY club_id'),{'l':league_id})).all()]
    while len(clubs) < 16:
        bot_id,_=await create_test_bot_club(s, is_test=not start_global_competitions)
        await s.execute(text('INSERT INTO league_teams(league_id,club_id) VALUES(:l,:c) ON CONFLICT DO NOTHING'),{'l':league_id,'c':bot_id})
        clubs.append(int(bot_id))
    clubs=clubs[:16]
    if len(clubs) != 16:
        raise ValueError('League must contain exactly 16 clubs.')

    await s.execute(text("UPDATE leagues SET max_teams=16,total_rounds=30,match_time='20:00',name=:name WHERE id=:l"), {
        'l': league_id,
        'name': f"FM27 {('Rookie' if str(league['tier'] or 'ROOKIE').upper()=='ROOKIE' else str(league['tier']).title())} League",
    })
    fixtures=round_robin(clubs,True)
    start_date=league['start_date'] or date.today()
    interval_minutes=league['match_interval_minutes']
    interval_days=max(1,int(league['match_interval_days'] or 1))
    kickoff_time=time(20,0)
    msk=timezone(timedelta(hours=3))
    kickoffs=[]
    if interval_minutes:
        # Test leagues use a rolling schedule anchored to the actual start
        # moment, not the ordinary leagues' fixed global kickoff slot.
        base_now=datetime.now(timezone.utc)
        first_match_at=base_now+timedelta(minutes=int(interval_minutes))
        for round_no in range(1,31):
            kickoffs.append(first_match_at+timedelta(
                minutes=int(interval_minutes)*(round_no-1)
            ))
    else:
        # Preserve the fixed local kickoff schedule for ordinary leagues.
        start_dt=datetime.combine(start_date,kickoff_time,tzinfo=msk)
        for round_no in range(1,31):
            candidate=start_date if round_no==1 else start_date+timedelta(days=interval_days*(round_no-1))
            while candidate.weekday()==6:
                candidate += timedelta(days=1)
            kickoffs.append(datetime.combine(candidate,kickoff_time,tzinfo=msk))
    season_start=kickoffs[0]
    season_id=(await s.execute(text("SELECT season_id FROM leagues WHERE id=:l"), {'l':league_id})).scalar_one_or_none()
    if season_id is None:
        season_id=(await s.execute(text("SELECT id FROM seasons WHERE state IN ('ACTIVE','PRESEASON') ORDER BY id DESC LIMIT 1"))).scalar_one_or_none()
        if season_id is None:
            # A test league must still be schedulable when no season is ACTIVE/PRESEASON.
            season_id=(await s.execute(text("SELECT id FROM seasons ORDER BY id DESC LIMIT 1"))).scalar_one_or_none()
        if season_id is None:
            raise ValueError('Нельзя запустить быстрый тест: в базе ещё не создан сезон.')
        await s.execute(text("UPDATE leagues SET season_id=:s WHERE id=:l"), {'s':int(season_id),'l':league_id})
    for f in fixtures:
        kickoff=kickoffs[f.round_no-1]
        kickoff=await get_next_free_date(s, kickoff, (f.home_club_id, f.away_club_id), priority=0, exact_time=bool(interval_minutes))
        if not isinstance(kickoff, datetime):
            kickoff=datetime.combine(kickoff, kickoff_time, tzinfo=msk)
        match_id=(await s.execute(text("""INSERT INTO matches(league_id,season_id,round,home_club_id,away_club_id,seed,competition_type,started_at,scheduled_at,prestart_notified_at)
            VALUES(:l,:s,:r,:h,:a,0,'LEAGUE',NULL,CAST(:kickoff AS timestamptz),NULL) RETURNING id"""),
            {'l':league_id,'s':int(season_id) if season_id is not None else None,'r':f.round_no,'h':f.home_club_id,'a':f.away_club_id,'kickoff':kickoff})).scalar_one()
        if season_id is not None:
            season_seed=int((await s.execute(text("SELECT season_seed FROM seasons WHERE id=:s"), {'s':int(season_id)})).scalar_one_or_none() or 0)
            await s.execute(text("UPDATE matches SET seed=:seed WHERE id=:m"), {'seed':season_seed+int(match_id),'m':int(match_id)})
        await queue_event(s,'MATCH_LEAGUE',kickoff,{'season_id':int(season_id),'league_id':int(league_id),'match_id':int(match_id)})
        for cid in (f.home_club_id,f.away_club_id):
            await queue_club_notification(s,cid,'MATCH','⏰ Ваш матч начнётся через 10 минут','Подготовьтесь к матчу',
                f'match-start-10:{league_id}:{f.round_no}:{cid}:{match_id}',kickoff-timedelta(minutes=10))
    await s.execute(text("UPDATE leagues SET status='ACTIVE',season_state='ACTIVE',current_round=1,total_rounds=30,started_at=CAST(:started AS timestamptz),match_time='20:00',max_teams=16 WHERE id=:l"),
                    {'started':season_start,'l':league_id})
    # League-only mode: no new Cup/Champions League tournaments are created.
    return league_id,len(fixtures),30


async def start_quick_test_league(s, admin_user_id):
    """Create an isolated 16-team league containing the admin's own club.

    The quick-test league is intentionally independent from the club's normal
    league membership: league_teams has no single-league ownership semantics,
    so the same club may participate in this isolated test competition while
    remaining in its regular league unchanged.
    """
    from app.game.season import ensure_initial_season
    season_id=(await s.execute(text("SELECT id FROM seasons WHERE state='ACTIVE' ORDER BY id DESC LIMIT 1"))).scalar_one_or_none()
    if season_id is None:
        season_id=await ensure_initial_season(s)
        if season_id is None:
            raise RuntimeError('Не удалось создать сезон для быстрого теста')
    season_id=int(season_id)

    owner=(await s.execute(text("""SELECT id FROM users WHERE telegram_id=:tg"""),
                           {'tg': int(admin_user_id)})).scalar_one_or_none()
    if owner is None:
        raise ValueError('Пользователь администратора не найден.')
    player_club=(await s.execute(text("""SELECT id FROM clubs WHERE owner_user_id=:u ORDER BY is_test ASC,id LIMIT 1"""),
                                 {'u': int(owner)})).scalar_one_or_none()
    if player_club is None:
        raise ValueError('У администратора нет клуба для быстрого теста.')

    row=(await s.execute(text("""INSERT INTO leagues(
        name,invite_code,creator_user_id,max_teams,total_rounds,tier,status,
        match_interval_days,match_interval_minutes,match_time,start_date,is_test
    ) VALUES(:name,:code,:creator,16,30,'ROOKIE','WAITING',1,10,'20:00',CURRENT_DATE,TRUE)
    RETURNING id"""), {
        'name': 'FM27 Quick Test 10m',
        'code': token_urlsafe(6).upper().replace('-','').replace('_','')[:8],
        'creator': int(owner),
    })).mappings().first()
    league_id=int(row['id'])

    # Always put the admin's real club into the test league first. This does
    # not remove it from, or alter, any existing league membership.
    await s.execute(text('INSERT INTO league_teams(league_id,club_id) VALUES(:l,:c)'),
                    {'l':league_id,'c':int(player_club)})

    # _start_league_by_id fills the remaining 15 slots with bot clubs.
    # Keep the quick test isolated: it must not spawn global Cup/Champions competitions.
    await _start_league_by_id(s, league_id, start_global_competitions=False)
    return league_id


async def auto_join_rookie_league(s, owner_user_id, club_id):
    """Put a player club into a 16-team Rookie league."""
    await s.execute(text("SELECT pg_advisory_xact_lock(hashtext('rookie-league-auto-join'))"))
    league=(await s.execute(text("""SELECT id,max_teams,status FROM leagues
        WHERE tier='ROOKIE' AND status='WAITING' AND max_teams>=16
        ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1"""))).mappings().first()
    if not league:
        # If Rookie leagues are already active, replace a bot-owned slot rather than
        # making the newcomer wait for the next season. Never select a real user's club.
        active=(await s.execute(text("""SELECT l.id AS league_id,lt.club_id,lt.played,lt.wins,lt.draws,lt.losses,
                lt.goals_for,lt.goals_against,lt.points
            FROM leagues l JOIN league_teams lt ON lt.league_id=l.id
            JOIN clubs bc ON bc.id=lt.club_id JOIN users bu ON bu.id=bc.owner_user_id
            WHERE l.tier='ROOKIE' AND l.status='ACTIVE' AND l.season_state='ACTIVE'
              AND bu.telegram_id<0
              AND NOT EXISTS (SELECT 1 FROM matches m WHERE m.league_id=l.id
                AND (m.home_club_id=lt.club_id OR m.away_club_id=lt.club_id)
                AND m.status IN ('LIVE','IN_PROGRESS'))
            ORDER BY lt.played ASC, l.id, lt.club_id FOR UPDATE OF l,lt SKIP LOCKED LIMIT 1"""))).mappings().first()
        if active:
            lid,bot_id=int(active['league_id']),int(active['club_id'])
            await s.execute(text("""INSERT INTO league_teams(league_id,club_id,played,wins,draws,losses,goals_for,goals_against,points)
                VALUES(:l,:c,:p,:w,:d,:lo,:gf,:ga,:pts)
                ON CONFLICT(league_id,club_id) DO NOTHING"""),
                {'l':lid,'c':club_id,'p':active['played'],'w':active['wins'],'d':active['draws'],'lo':active['losses'],'gf':active['goals_for'],'ga':active['goals_against'],'pts':active['points']})
            # Keep played results immutable; only unscheduled future fixtures transfer to the human club.
            await s.execute(text("""UPDATE matches SET home_club_id=:new WHERE league_id=:l AND home_club_id=:old
                AND scheduled_at>now() AND status NOT IN ('LIVE','IN_PROGRESS','FINISHED','FAILED')"""), {'new':club_id,'l':lid,'old':bot_id})
            await s.execute(text("""UPDATE matches SET away_club_id=:new WHERE league_id=:l AND away_club_id=:old
                AND scheduled_at>now() AND status NOT IN ('LIVE','IN_PROGRESS','FINISHED','FAILED')"""), {'new':club_id,'l':lid,'old':bot_id})
            await s.execute(text('DELETE FROM league_teams WHERE league_id=:l AND club_id=:c'),{'l':lid,'c':bot_id})
            return lid,'REPLACED_BOT'
        code=token_urlsafe(6).upper().replace('-','').replace('_','')[:8]
        row=(await s.execute(text("""INSERT INTO leagues(name,invite_code,creator_user_id,max_teams,total_rounds,tier,status,match_interval_days,match_time,start_date)
            VALUES(:n,:code,:u,16,30,'ROOKIE','WAITING',1,'20:00',CURRENT_DATE) RETURNING id"""),
            {'n':f'FM27 Rookie {code}','code':code,'u':owner_user_id})).first()
        league={'id':row.id,'max_teams':16,'status':'WAITING'}
    already=(await s.execute(text('SELECT 1 FROM league_teams WHERE league_id=:l AND club_id=:c'),{'l':league['id'],'c':club_id})).first()
    if already: return league['id'],'WAITING'
    count=int((await s.execute(text('SELECT count(*) FROM league_teams WHERE league_id=:l'),{'l':league['id']})).scalar_one())
    if count>=16: return league['id'],'FULL'
    await s.execute(text('INSERT INTO league_teams(league_id,club_id) VALUES(:l,:c)'),{'l':league['id'],'c':club_id})
    count += 1
    if count >= 16:
        await _start_league_by_id(s,league['id'])
        logger.info('Лига [ROOKIE] набрана: league_id=%s',league['id'])
        return league['id'],'ACTIVE'
    return league['id'],'WAITING'


async def fill_waiting_rookie_leagues(s):
    """Fill waiting Rookie leagues with bot clubs until 16 and start them."""
    leagues=(await s.execute(text("""SELECT l.id,l.max_teams,l.created_at,count(t.club_id) member_count
        FROM leagues l LEFT JOIN league_teams t ON t.league_id=l.id
        WHERE l.tier='ROOKIE' AND l.status='WAITING'
          AND l.created_at <= now()-INTERVAL '24 hours'
        GROUP BY l.id HAVING count(t.club_id)<16 ORDER BY l.id"""))).mappings().all()
    filled=0
    for league in leagues:
        count=int(league['member_count'])
        while count < 16:
            bot_id,_=await create_test_bot_club(s)
            await s.execute(text('INSERT INTO league_teams(league_id,club_id) VALUES(:l,:c)'),{'l':league['id'],'c':bot_id})
            count += 1; filled += 1
        if count >= 16:
            await _start_league_by_id(s,league['id'])
    return filled,len(leagues)


async def create_league(s, creator_user_id, name, max_teams=16):
    club=(await s.execute(text('SELECT id FROM clubs WHERE owner_user_id=:u'),{'u':creator_user_id})).scalar_one_or_none()
    if not club: raise ValueError('Create a club first.')
    await ensure_squad(s,club)
    code=token_urlsafe(6).upper().replace('-','').replace('_','')[:8]
    row=(await s.execute(text("""INSERT INTO leagues(name,invite_code,creator_user_id,max_teams,total_rounds,tier)
        VALUES(:n,:code,:u,:m,:r,'ROOKIE') RETURNING id"""),
        {'n':name,'code':code,'u':creator_user_id,'m':max_teams,'r':max_teams*2-2})).first()
    await s.execute(text('INSERT INTO league_teams(league_id,club_id) VALUES(:l,:c)'),{'l':row.id,'c':club})
    return row.id,code

async def join_league(s,user_id,code):
    club=(await s.execute(text('SELECT id FROM clubs WHERE owner_user_id=:u'),{'u':user_id})).scalar_one_or_none()
    if not club: raise ValueError('Create a club first.')
    league=(await s.execute(text("SELECT id,max_teams,status FROM leagues WHERE invite_code=:code FOR UPDATE"),{'code':code.upper()})).mappings().first()
    if not league: raise ValueError('League not found.')
    if league['status']!='WAITING': raise ValueError('League has already started.')
    n=(await s.execute(text('SELECT count(*) FROM league_teams WHERE league_id=:l'),{'l':league['id']})).scalar_one()
    if n>=league['max_teams']: raise ValueError('League is full.')
    await ensure_squad(s,club)
    if (await s.execute(text('SELECT 1 FROM league_teams WHERE league_id=:l AND club_id=:c'),{'l':league['id'],'c':club})).first():
        raise ValueError('Your club is already in this league.')
    await s.execute(text('INSERT INTO league_teams(league_id,club_id) VALUES(:l,:c)'),{'l':league['id'],'c':club})
    return league['id']

async def start_league(s, user_id):
    league=(await s.execute(text("SELECT id FROM leagues WHERE creator_user_id=:u AND status='WAITING' ORDER BY id DESC LIMIT 1 FOR UPDATE"),{'u':user_id})).mappings().first()
    if not league: raise ValueError('No waiting league found.')
    return await _start_league_by_id(s,league['id'])

def _player_value(player, key, default=0):
    if isinstance(player, dict):
        return player.get(key, default) or default
    return getattr(player, key, default) or default


_AUTOSQUAD_STATS = {
    'GK': ('defending', 'mental'),
    'CB': ('defending', 'physical', 'mental'),
    'LB': ('defending', 'pace', 'stamina'),
    'RB': ('defending', 'pace', 'stamina'),
    'DM': ('defending', 'passing', 'physical'),
    'CM': ('passing', 'dribbling', 'mental'),
    'AM': ('passing', 'dribbling', 'shooting'),
    'LW': ('pace', 'dribbling', 'shooting'),
    'RW': ('pace', 'dribbling', 'shooting'),
    'LM': ('passing', 'pace', 'stamina'),
    'RM': ('passing', 'pace', 'stamina'),
    'ST': ('shooting', 'pace', 'physical'),
}


def _secondary_positions(player):
    value=_player_value(player, 'secondary_positions', ())
    if isinstance(value, str):
        try:
            import json as _json
            parsed=_json.loads(value)
            if isinstance(parsed, list): value=parsed
        except Exception:
            value=[x.strip() for x in value.split(',') if x.strip()]
    return set(value or ())


def _relevant_score(player, slot):
    return sum(float(_player_value(player, stat)) for stat in _AUTOSQUAD_STATS.get(slot, ('mental',)))


def _build_position_lineup(players, slots):
    """Build an ordered XI, preferring exact and secondary positions before fallback."""
    available=list(players)
    chosen=[]
    used=set()
    for slot in slots:
        exact=[p for p in available if _player_value(p, 'id') not in used and _player_value(p, 'position') == slot]
        secondary=[p for p in available if _player_value(p, 'id') not in used and slot in _secondary_positions(p)]
        pool=exact or secondary
        if not pool:
            pool=[p for p in available if _player_value(p, 'id') not in used]
        if not pool:
            raise ValueError(f'No available player for {slot}.')
        p=max(pool, key=lambda x: (_relevant_score(x, slot), float(_player_value(x, 'potential'))))
        chosen.append(p)
        used.add(_player_value(p, 'id'))
    return chosen



_BENCH_TEMPLATE_SLOTS = ['GK','CB','LB','RB','DM','CM','CM','AM','LW','RW','ST','ST']
_TEMPLATE_STARTING_SLOTS = {
    '4-3-3': ['GK','LB','CB','CB','RB','CM','DM','CM','LW','ST','RW'],
    '4-4-2': ['GK','LB','CB','CB','RB','LM','CM','CM','RM','ST','ST'],
    '4-2-3-1': ['GK','LB','CB','CB','RB','DM','DM','LW','AM','RW','ST'],
    '3-5-2': ['GK','CB','CB','CB','LM','CM','DM','CM','RM','ST','ST'],
    '5-3-2': ['GK','LB','CB','CB','CB','RB','CM','DM','CM','ST','ST'],
    '4-1-2-1-2': ['GK','LB','CB','CB','RB','DM','CM','CM','AM','ST','ST'],
    '4-3-1-2': ['GK','LB','CB','CB','RB','CM','CM','CM','AM','ST','ST'],
    '4-5-1': ['GK','LB','CB','CB','RB','LM','CM','CM','CM','RM','ST'],
    '3-4-3': ['GK','CB','CB','CB','LM','CM','CM','RM','LW','ST','RW'],
    '4-1-4-1': ['GK','LB','CB','CB','RB','DM','LM','CM','CM','RM','ST'],
}
_TEMPLATE_SLOTS = {f: slots + _BENCH_TEMPLATE_SLOTS for f, slots in _TEMPLATE_STARTING_SLOTS.items()}
STARTING_SLOT_RARITIES = tuple('RARE' if i in {2,3,6,7,8,10} else 'EPIC' if i in {5,9} else 'BASE' for i in range(23))


async def create_player_copy(s, template_id):
    template=(await s.execute(text('SELECT id FROM players WHERE id=:t AND is_template=TRUE FOR SHARE'), {'t':template_id})).first()
    if not template:
        raise ValueError('Player template not found.')
    row=(await s.execute(text("""INSERT INTO players(
        first_name,last_name,nationality,age,position,pace,shooting,passing,dribbling,
        defending,physical,stamina,mental,market_value,salary,potential,rarity,card_version,
        real_player,preferred_foot,secondary_positions,portrait_url,portrait_source,talents,
        tactical_archetype,data_source,portrait_license,portrait_author,portrait_url_hash,
        is_template,template_id
    ) SELECT
        first_name,last_name,nationality,age,position,pace,shooting,passing,dribbling,
        defending,physical,stamina,mental,market_value,salary,potential,rarity,card_version,
        real_player,preferred_foot,secondary_positions,NULL,NULL,talents,
        tactical_archetype,'copy',NULL,NULL,NULL,FALSE,:t
      FROM players WHERE id=:t AND is_template=TRUE
      RETURNING id"""), {'t':template_id})).first()
    if not row:
        raise ValueError('Player template not found.')
    logger.info('Created player copy player_id=%s template_id=%s', row.id, template_id)
    return int(row.id)


async def club_has_template(s, club_id, template_id):
    return (await s.execute(text("""SELECT 1 FROM club_players cp
        JOIN players p ON p.id=cp.player_id
        WHERE cp.club_id=:c AND COALESCE(cp.template_id,p.template_id)=:t LIMIT 1"""),
        {'c':club_id,'t':template_id})).first() is not None


async def add_copy_to_club(s, club_id, template_id, shirt_number=None):
    template=(await s.execute(text('SELECT market_value,salary FROM players WHERE id=:t AND is_template=TRUE FOR SHARE'), {'t':template_id})).mappings().first()
    if not template:
        raise ValueError('Player template not found.')
    if await club_has_template(s, club_id, template_id):
        # A duplicate template is a no-op. Callers such as the starting-squad
        # builder must be able to skip it and choose another template; no
        # compensation is appropriate because no copy was created.
        return None, 0
    new_id=await create_player_copy(s, template_id)
    inserted=(await s.execute(text("""INSERT INTO club_players(
        club_id,player_id,template_id,shirt_number,fitness,form,morale,contract_salary,release_clause,contract_until)
        VALUES(:c,:p,:t,:shirt,100,0,70,:salary,:clause,CURRENT_DATE+INTERVAL '3 years')
        ON CONFLICT (club_id,template_id) DO NOTHING
        RETURNING player_id"""), {
        'c':club_id,'p':new_id,'t':template_id,'shirt':shirt_number,
        'salary':int(template['salary']),'clause':int(template['market_value'])*2,
    })).scalar_one_or_none()
    if inserted is None:
        # The unique index won a race: another transaction already owns this
        # template for the club. Treat it exactly like the pre-check above.
        await s.execute(text('DELETE FROM players WHERE id=:p AND is_template=FALSE'), {'p':new_id})
        return None, 0
    return int(inserted), 0


async def _template_usage_counts(s, club_id):
    rows=(await s.execute(text("""SELECT p.template_id,COUNT(*) AS uses
        FROM club_players cp JOIN players p ON p.id=cp.player_id
        WHERE cp.club_id<>:c AND p.template_id IS NOT NULL
        GROUP BY p.template_id"""), {'c':club_id})).all()
    return {int(r.template_id):int(r.uses) for r in rows}


async def _pick_template(s, used_templates, rarity_order, positions, usage):
    excluded={int(tid) for tid in (used_templates or set())}
    for rarity in rarity_order:
        rows=(await s.execute(text("""SELECT id FROM players
            WHERE is_template=TRUE AND rarity=:rarity
              AND position=ANY(:positions)
              AND NOT (id=ANY(:used))"""), {
            'rarity':rarity,
            'positions':list(positions),
            'used':list(excluded) or [0],
        })).scalars().all()
        # Keep the invariant in Python as a second guard even if a DB driver
        # changes ARRAY/ANY binding semantics.
        rows=[int(pid) for pid in rows if int(pid) not in excluded]
        if rows:
            return min(rows, key=lambda pid:(usage.get(int(pid),0), int(pid)))
    return None


async def _add_template_copies_batch(s, club_id, template_ids, shirt_numbers):
    """Create all requested copies for one club with batched DB writes."""
    template_ids=[int(t) for t in template_ids]
    if not template_ids:
        return []
    existing=(await s.execute(text("""SELECT COALESCE(cp.template_id,p.template_id) AS template_id
        FROM club_players cp JOIN players p ON p.id=cp.player_id
        WHERE cp.club_id=:c AND COALESCE(cp.template_id,p.template_id)=ANY(:tids)"""),
        {'c':club_id,'tids':template_ids})).scalars().all()
    existing={int(t) for t in existing if t is not None}
    template_ids=[t for t in template_ids if t not in existing]
    if not template_ids:
        return []

    # Copy player templates in one INSERT. Every source row is explicitly
    # restricted to is_template=TRUE and carries its source template_id.
    template_rows={int(r['id']):dict(r) for r in (await s.execute(
        text('SELECT id,salary,market_value FROM players WHERE id=ANY(:tids) AND is_template=TRUE'),
        {'tids':template_ids})).mappings().all()}
    if len(template_rows) != len(template_ids):
        raise RuntimeError('One or more selected template rows disappeared before batch creation.')
    created=(await s.execute(text("""INSERT INTO players(
        first_name,last_name,nationality,age,position,pace,shooting,passing,dribbling,
        defending,physical,stamina,mental,market_value,salary,potential,rarity,card_version,
        real_player,preferred_foot,secondary_positions,portrait_url,portrait_source,talents,
        tactical_archetype,data_source,portrait_license,portrait_author,portrait_url_hash,
        is_template,template_id
    ) SELECT
        p.first_name,p.last_name,p.nationality,p.age,p.position,p.pace,p.shooting,p.passing,p.dribbling,
        p.defending,p.physical,p.stamina,p.mental,p.market_value,p.salary,p.potential,p.rarity,p.card_version,
        p.real_player,p.preferred_foot,p.secondary_positions,NULL,NULL,p.talents,
        p.tactical_archetype,'copy',NULL,NULL,NULL,FALSE,p.id
      FROM players p
      WHERE p.id=ANY(:tids) AND p.is_template=TRUE
      RETURNING id,template_id"""), {'tids':template_ids})).mappings().all()
    by_template={int(r['template_id']):int(r['id']) for r in created}
    if len(by_template)!=len(template_ids):
        raise RuntimeError('Could not create all requested player copies from templates.')
    values=[]
    params={}
    for i,tid in enumerate(template_ids):
        values.append(f"(:c{i},:p{i},:t{i},:shirt{i},100,0,70,:salary{i},:clause{i})")
        params.update({f'c{i}':club_id, f'p{i}':by_template[tid], f't{i}':tid,
                       f'shirt{i}':shirt_numbers[tid], f'salary{i}':int(template_rows[tid]['salary']),
                       f'clause{i}':int(template_rows[tid]['market_value'])*2})
    inserted=(await s.execute(text(f"""INSERT INTO club_players(
        club_id,player_id,template_id,shirt_number,fitness,form,morale,contract_salary,release_clause)
        VALUES {','.join(values)}
        ON CONFLICT (club_id,template_id) DO NOTHING
        RETURNING player_id,template_id"""), params)).mappings().all()
    inserted_templates={int(r['template_id']) for r in inserted}
    if inserted_templates != set(template_ids):
        # A concurrent owner won the unique-index race. Remove only copies that
        # were not attached to this club, then report the successful subset.
        orphan_ids=[by_template[t] for t in template_ids if t not in inserted_templates]
        if orphan_ids:
            await s.execute(text('DELETE FROM players WHERE id=ANY(:ids) AND is_template=FALSE'), {'ids':orphan_ids})
    for row in inserted:
        logger.info('Created player copy player_id=%s template_id=%s', int(row['player_id']), int(row['template_id']))
    return [(int(r['player_id']), int(r['template_id'])) for r in inserted]


async def _build_starting_squad_from_templates(s, club_id, formation='4-3-3'):
    """Create a 23-player squad using the requested formation's slot profile."""
    # Serialize squad construction per club. This also covers a live worker
    # racing with an admin full_reset after the reset clears club_players.
    await s.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('football_manager_squad:' || :club_id, 0))"), {'club_id':str(club_id)})
    if formation not in ALLOWED_FORMATIONS:
        raise ValueError(f'Unsupported formation: {formation}')
    existing=int((await s.execute(text('SELECT COUNT(*) FROM club_players WHERE club_id=:c'), {'c':club_id})).scalar_one())
    if existing:
        if existing != 23:
            raise ValueError('Club already has a non-standard squad; reset it before rebuilding.')
        return existing
    usage=await _template_usage_counts(s,club_id)
    slots=list(_TEMPLATE_SLOTS[formation])
    used=set()
    skipped=set()
    selected=[]
    slot_meta=[]
    final_templates={}
    for idx,pos in enumerate(slots):
        target_rarity=STARTING_SLOT_RARITIES[idx]
        if target_rarity == 'EPIC':
            rarity_order=('EPIC','RARE','BASE')
        elif target_rarity == 'RARE':
            rarity_order=('RARE','EPIC','BASE')
        else:
            rarity_order=('BASE','RARE','EPIC')
        tid=await _pick_template(s,used | skipped,rarity_order,{pos},usage)
        if tid is None:
            raise RuntimeError(f'No unique template available for {pos}.')
        selected.append(tid)
        slot_meta.append((idx,pos,rarity_order,tid))
        final_templates[idx]=tid
        # Reserve the template immediately so this same selection pass can never
        # select it again. It is only a logical reservation; successful DB
        # ownership is confirmed below after the batch INSERT.
        used.add(tid)

    if len(selected) != 23 or len(set(selected)) != 23:
        raise RuntimeError('Could not select 23 unique templates for the squad.')

    # Rebuild `used` from actual successful ownership before handling races.
    used.clear()
    pending=slot_meta
    created_by_template={}
    while pending:
        batch_templates=[tid for _,_,_,tid in pending]
        shirt_numbers={tid:idx+1 for idx,_,_,tid in pending}
        created=await _add_template_copies_batch(s,club_id,batch_templates,shirt_numbers)
        batch_created={tid:pid for pid,tid in created}
        created_by_template.update(batch_created)
        missing=[item for item in pending if item[3] not in batch_created]
        for item in pending:
            if item[3] in batch_created:
                used.add(item[3])
        if not missing:
            break
        skipped.update(item[3] for item in missing)
        replacement_pending=[]
        for idx,pos,rarity_order,_old_tid in missing:
            tid=await _pick_template(s,used | skipped,rarity_order,{pos},usage)
            if tid is None:
                raise RuntimeError(f'No unique template available for {pos}.')
            replacement_pending.append((idx,pos,rarity_order,tid))
            final_templates[idx]=tid
            # Reserve replacement immediately so two missing slots cannot pick it.
            skipped.add(tid)
        # The replacement is now a candidate for the next batch, but it must be
        # considered selected only after that batch succeeds.
        pending=replacement_pending

    if len(created_by_template) != 23 or len(set(created_by_template)) != 23:
        raise RuntimeError('Could not create 23 unique player copies for the squad.')
    created_ids=[created_by_template[final_templates[idx]] for idx in range(23)]
    if len(created_ids) != 23 or len(set(created_by_template)) != 23:
        raise RuntimeError('Could not create all 23 unique player copies for the squad.')
    await s.execute(text('INSERT INTO club_lineups(club_id,player_ids) VALUES(:c,:ids) ON CONFLICT(club_id) DO UPDATE SET player_ids=EXCLUDED.player_ids'),
                    {'c':club_id,'ids':created_ids[:11]})
    return 23


def build_starting_lineup(first, second, formation='4-3-3'):
    if hasattr(first, 'execute'):
        return _build_starting_squad_from_templates(first, second, formation)
    return _build_position_lineup(first, second)


async def _assign_fresh_leagues_to_real_players(s, season_id: int) -> int:
    """Create fresh Rookie leagues, assigning real-user clubs before bot fillers."""
    humans = (await s.execute(text("""
        SELECT DISTINCT ON (c.owner_user_id) c.id AS club_id, c.owner_user_id
        FROM clubs c JOIN users u ON u.id=c.owner_user_id
        WHERE u.telegram_id > 0 AND NOT COALESCE(c.is_test,FALSE)
        ORDER BY c.owner_user_id, c.is_test ASC, c.id ASC
    """))).mappings().all()
    if not humans:
        logger.warning('Full reset: no real non-test player clubs; no leagues created')
        return 0

    bot_ids = [int(r[0]) for r in (await s.execute(text("""
        SELECT c.id FROM clubs c JOIN users u ON u.id=c.owner_user_id
        WHERE u.telegram_id < 0 AND NOT COALESCE(c.is_test,FALSE) ORDER BY c.id
    """))).all()]
    start_date = datetime.now(timezone(timedelta(hours=3))).date() + timedelta(days=1)
    humans = [dict(row) for row in humans]
    league_count = 0

    for offset in range(0, len(humans), 16):
        group = humans[offset:offset + 16]
        code = token_urlsafe(9).upper().replace('-', '').replace('_', '')[:12]
        league_id = int((await s.execute(text("""
            INSERT INTO leagues(
                name, invite_code, creator_user_id, max_teams, total_rounds, tier,
                status, season_id, season_number, season_state, match_interval_days,
                match_time, start_date, is_test
            ) VALUES(
                :name, :code, :creator, 16, 30, 'ROOKIE', 'WAITING',
                :season_id, 1, 'PRESEASON', 1, '20:00', :start_date, FALSE
            ) RETURNING id
        """), {
            'name': f'FM27 Rookie Reset {league_count + 1}',
            'code': code,
            'creator': int(group[0]['owner_user_id']),
            'season_id': int(season_id),
            'start_date': start_date,
        })).scalar_one())

        member_ids = [int(row['club_id']) for row in group]
        for club_id in member_ids:
            await s.execute(text("""
                INSERT INTO league_teams(league_id,club_id,played,wins,draws,losses,goals_for,goals_against,points)
                VALUES(:l,:c,0,0,0,0,0,0,0)
            """), {'l': league_id, 'c': club_id})

        while len(member_ids) < 16:
            if bot_ids:
                bot_id = bot_ids.pop(0)
            else:
                bot_id, _ = await create_test_bot_club(s, is_test=False)
                bot_id = int(bot_id)
            await s.execute(text("""
                INSERT INTO league_teams(league_id,club_id,played,wins,draws,losses,goals_for,goals_against,points)
                VALUES(:l,:c,0,0,0,0,0,0,0)
            """), {'l': league_id, 'c': bot_id})
            member_ids.append(bot_id)

        # Competition creation is intentionally league-only after full reset.
        await _start_league_by_id(s, league_id, start_global_competitions=False)
        league_count += 1

    unassigned = int((await s.execute(text("""
        SELECT COUNT(*) FROM (
            SELECT DISTINCT ON (c.owner_user_id) c.id AS club_id
            FROM clubs c JOIN users u ON u.id=c.owner_user_id
            WHERE u.telegram_id > 0 AND NOT COALESCE(c.is_test,FALSE)
            ORDER BY c.owner_user_id,c.is_test ASC,c.id ASC
        ) human
        WHERE NOT EXISTS (
            SELECT 1 FROM league_teams lt JOIN leagues l ON l.id=lt.league_id
            WHERE lt.club_id=human.club_id AND l.status='ACTIVE' AND l.tier='ROOKIE'
        )
    """))).scalar_one())
    if unassigned:
        raise RuntimeError(f'Full reset invariant failed: {unassigned} real player clubs were not assigned to a fresh league')

    duplicates = int((await s.execute(text("""
        SELECT COUNT(*) FROM (
            SELECT c.id FROM clubs c JOIN users u ON u.id=c.owner_user_id
            JOIN league_teams lt ON lt.club_id=c.id
            WHERE u.telegram_id > 0 AND NOT COALESCE(c.is_test,FALSE)
            GROUP BY c.id HAVING COUNT(*) > 1
        ) duplicate_memberships
    """))).scalar_one())
    if duplicates:
        raise RuntimeError(f'Full reset invariant failed: {duplicates} real clubs have duplicate league memberships')

    logger.info('Full reset league placement complete: real_clubs=%s, leagues=%s', len(humans), league_count)
    return league_count


async def full_reset(s, admin_user_id=None):
    """Reset all season and club state without running normal season-end rewards."""
    # Serialize resets across callbacks/admin sessions.
    await s.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('football_manager_full_reset', 0))"))
    club_ids=[int(r[0]) for r in (await s.execute(text('SELECT id FROM clubs ORDER BY id'))).all()]
    for club_id in club_ids:
        await s.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('football_manager_squad:' || :club_id, 0))"), {'club_id':str(club_id)})

    # FK order follows the actual migrations: match children first; snapshot
    # rows before seasons/sequences because their foreign keys use ON DELETE RESTRICT.
    # Stage 2: match-dependent rows.
    for sql in (
        'DELETE FROM match_player_stats',
        'DELETE FROM match_player_instructions',
        'DELETE FROM match_events',
        'DELETE FROM match_substitutions',
        'DELETE FROM match_tactics',
        'DELETE FROM match_lineups',
        'DELETE FROM league_match_finalizations',
    ):
        await s.execute(text(sql))

    # Stage 3: club configuration and development helpers.
    for sql in (
        'DELETE FROM club_substitution_plans',
        'DELETE FROM club_counterplans',
        'DELETE FROM club_player_instructions',
        'DELETE FROM club_lineups',
        'DELETE FROM club_tactics',
        'DELETE FROM club_training',
        'DELETE FROM player_development_log',
        'DELETE FROM player_history',
        'DELETE FROM pack_history',
    ):
        await s.execute(text(sql))

    # Stage 4: competition children before their parent records.
    for sql in (
        'DELETE FROM cup_matches',
        'DELETE FROM cups',
        'DELETE FROM champions_matches',
        'DELETE FROM champions_groups',
        'DELETE FROM champions_league',
    ):
        await s.execute(text(sql))

    # Stage 5: market, notifications, user progress and finance ledgers.
    for sql in (
        'DELETE FROM transfer_offers',
        'DELETE FROM transfer_listings',
        'DELETE FROM transfers',
        'DELETE FROM shortlist',
        'DELETE FROM inbox_messages',
        'DELETE FROM notifications',
        'DELETE FROM daily_quests',
        'DELETE FROM club_inventory',
        'DELETE FROM club_financial_transactions',
        'DELETE FROM club_season_finances',
    ):
        await s.execute(text(sql))
    # Preserve Battle Pass configuration/reward definitions and claimed-reward
    # markers; only reset XP/level, so rerunning Full Reset cannot re-grant claims.
    await s.execute(text('UPDATE season_pass_progress SET points=0,level=0'))

    # Stage 6: history/events. Snapshot deletion is intentionally permitted only
    # inside Full Reset, so temporarily drop and then restore its immutable trigger.
    await s.execute(text('DROP TRIGGER IF EXISTS trg_season_league_snapshots_immutable ON season_league_snapshots'))
    for sql in (
        'DELETE FROM season_events',
        'DELETE FROM liveops_events',
        'DELETE FROM liveops_season_config',
        'DELETE FROM league_history',
        'DELETE FROM season_league_snapshots',
        'DELETE FROM player_season_state',
        'DELETE FROM season_history',
    ):
        await s.execute(text(sql))
    await s.execute(text("CREATE TRIGGER trg_season_league_snapshots_immutable BEFORE UPDATE OR DELETE ON season_league_snapshots FOR EACH ROW EXECUTE FUNCTION prevent_season_league_snapshot_mutation()"))

    # Stage 7: standings, schedule and club/player ownership links.
    for sql in (
        'DELETE FROM league_teams',
        'DELETE FROM matches',
        'DELETE FROM club_players',
        'DELETE FROM players WHERE is_template=FALSE',
    ):
        await s.execute(text(sql))

    # Stage 8: leagues -> seasons -> sequences. seasons.sequence_id is RESTRICT.
    await s.execute(text('DELETE FROM leagues'))
    await s.execute(text('DELETE FROM seasons'))
    await s.execute(text('DELETE FROM season_sequences'))

    await s.execute(text("UPDATE clubs SET budget=2000000,fans=5000,reputation=1,stadium_level=1,ticket_price=20,sponsor_level=1,debt=0,staff_training=1,staff_medical=1,staff_scouting=1"))
    await s.execute(text('UPDATE players SET is_template=TRUE,template_id=NULL WHERE is_seed=TRUE'))

    # Do not change ensure_initial_season semantics.
    from app.game.season import ensure_initial_season
    await ensure_initial_season(s)

    # Rebuild retained squads first; assign every real user's primary club before
    # using any bot clubs to fill free seats in the new Rookie leagues.
    for club_id in club_ids:
        await build_starting_lineup(s,club_id)
        await s.execute(text('INSERT INTO club_training(club_id) VALUES(:c) ON CONFLICT DO NOTHING'), {'c':club_id})
        await s.execute(text('INSERT INTO club_tactics(club_id) VALUES(:c) ON CONFLICT DO NOTHING'), {'c':club_id})

    initial_season_id = (await s.execute(text("SELECT id FROM seasons WHERE state='PRESEASON' ORDER BY id DESC LIMIT 1"))).scalar_one()
    await _assign_fresh_leagues_to_real_players(s, int(initial_season_id))

    checks={
        'matches_without_season': "SELECT count(*) FROM matches m LEFT JOIN seasons s ON s.id=m.season_id WHERE s.id IS NULL",
        'active_leagues_without_season': "SELECT count(*) FROM leagues l LEFT JOIN seasons s ON s.id=l.season_id WHERE l.status='ACTIVE' AND s.id IS NULL",
        'duplicate_league_memberships': "SELECT count(*) FROM (SELECT league_id,club_id FROM league_teams GROUP BY league_id,club_id HAVING count(*)>1) d",
        'initial_seasons': "SELECT count(*) FROM seasons WHERE season_number=1 AND state='PRESEASON'",
    }
    for label,query in checks.items():
        value=int((await s.execute(text(query))).scalar_one())
        if label=='initial_seasons' and value!=1:
            raise RuntimeError(f'Full reset invariant failed: {label}={value}, expected 1')
        if label!='initial_seasons' and value:
            raise RuntimeError(f'Full reset invariant failed: {label}={value}, expected 0')
    logger.info('Full reset completed: clubs=%s; Battle Pass definitions and claimed-reward markers preserved',len(club_ids))
    await s.execute(text("""INSERT INTO admin_actions_log(admin_user_id,action,target_type,target_id,payload)
        VALUES(:u,'full_reset','system',NULL,CAST(:p AS jsonb))"""), {
        'u':admin_user_id,'p':json.dumps({'clubs_reset':len(club_ids)},ensure_ascii=False),
    })
    return len(club_ids)
async def _starting_players(s,club_id):
    rows=(await s.execute(text(f"SELECT {PLAYER_FIELDS},cp.fitness,cp.form,cp.morale,cp.is_injured,cp.injury_matches,cp.suspension_matches, p.secondary_positions FROM club_players cp JOIN players p ON p.id=cp.player_id WHERE cp.club_id=:c AND cp.is_injured=false AND cp.suspension_matches=0 ORDER BY p.potential DESC,p.id"),{'c':club_id})).mappings().all()
    by_id={r['id']:row_player(r) for r in rows}
    tactics=await _club_tactics(s,club_id)
    slots=[slot for slot,_,_ in FORMATION_SLOTS[tactics.formation]]
    saved=(await s.execute(text('SELECT player_ids FROM club_lineups WHERE club_id=:c'),{'c':club_id})).scalar_one_or_none()
    if saved:
        chosen=[]; seen=set()
        for pid in saved:
            pid=int(pid)
            if pid in by_id and pid not in seen:
                chosen.append(by_id[pid]); seen.add(pid)
            if len(chosen)==11: break
        if len(chosen)==11:
            return chosen

    # A missing/invalid saved lineup is repaired once and persisted. This makes
    # club_lineups the single source of truth for the board and starting XI.
    rows_by_id={r['id']:r for r in rows}
    lineup_rows=build_starting_lineup(list(rows_by_id.values()), slots)
    player_ids=[int(r['id']) for r in lineup_rows]
    await s.execute(text('''INSERT INTO club_lineups(club_id,player_ids)
        VALUES(:c,:ids)
        ON CONFLICT(club_id) DO UPDATE SET player_ids=EXCLUDED.player_ids'''),
        {'c':club_id,'ids':player_ids})
    return [by_id[pid] for pid in player_ids]

async def _bench_players(s,club_id,starting_ids=None):
    ids=list(starting_ids or []) or [0]
    saved=(await s.execute(text('SELECT bench_ids FROM club_lineups WHERE club_id=:c'),{'c':club_id})).scalar_one_or_none() or []
    saved_ids=[]
    for pid in saved:
        pid=int(pid)
        if pid not in ids and pid not in saved_ids:
            saved_ids.append(pid)
        if len(saved_ids)>=7:
            break
    if saved_ids:
        rows=(await s.execute(text(f"SELECT {PLAYER_FIELDS},cp.fitness,cp.form,cp.morale,cp.is_injured,cp.injury_matches,cp.suspension_matches FROM club_players cp JOIN players p ON p.id=cp.player_id WHERE cp.club_id=:c AND p.id=ANY(:bench) AND cp.is_injured=false AND cp.suspension_matches=0 AND NOT (p.id=ANY(:ids)) ORDER BY array_position(:bench,p.id)"),{'c':club_id,'bench':saved_ids,'ids':ids})).mappings().all()
        return [row_player(r) for r in rows]
    # Backwards-compatible fallback for older clubs without a selected bench.
    rows=(await s.execute(text(f"SELECT {PLAYER_FIELDS},cp.fitness,cp.form,cp.morale,cp.is_injured,cp.injury_matches,cp.suspension_matches FROM club_players cp JOIN players p ON p.id=cp.player_id WHERE cp.club_id=:c AND cp.is_injured=false AND cp.suspension_matches=0 AND NOT (p.id=ANY(:ids)) ORDER BY p.potential DESC,p.id LIMIT 7"),{'c':club_id,'ids':ids})).mappings().all()
    return [row_player(r) for r in rows]

async def set_starting_lineup(s, club_id, player_ids):
    if len(player_ids)!=11 or len(set(player_ids))!=11: raise ValueError('Lineup must contain exactly 11 different players.')
    rows=(await s.execute(text('SELECT p.id,p.position,cp.is_injured FROM club_players cp JOIN players p ON p.id=cp.player_id WHERE cp.club_id=:c AND p.id=ANY(:ids)'),{'c':club_id,'ids':player_ids})).mappings().all()
    if len(rows)!=11: raise ValueError('Every lineup player must belong to your club.')
    if any(r['is_injured'] for r in rows): raise ValueError('An injured player cannot start.')
    await s.execute(text('INSERT INTO club_lineups(club_id,player_ids) VALUES(:c,:ids) ON CONFLICT(club_id) DO UPDATE SET player_ids=EXCLUDED.player_ids'),{'c':club_id,'ids':player_ids})
    return player_ids

async def plan_substitution(s, club_id, minute, player_off_id, player_on_id):
    if not 46<=minute<=90: raise ValueError('Substitution minute must be 46..90.')
    rows=(await s.execute(text('SELECT player_id,is_injured FROM club_players WHERE club_id=:c AND player_id=ANY(:ids)'),{'c':club_id,'ids':[player_off_id,player_on_id]})).mappings().all()
    if player_off_id == player_on_id or len(rows)!=2: raise ValueError('Both substitution players must be different and belong to your club.')
    if any(r['is_injured'] for r in rows): raise ValueError('Injured players cannot be part of a substitution plan.')
    lineup=(await s.execute(text('SELECT player_ids FROM club_lineups WHERE club_id=:c'),{'c':club_id})).scalar_one_or_none() or []
    if player_off_id not in lineup: raise ValueError('The player coming off must be in the starting XI.')
    if player_on_id in lineup: raise ValueError('The player coming on must be a bench player.')
    prior=(await s.execute(text('''SELECT minute,player_off_id,player_on_id FROM club_substitution_plans
        WHERE club_id=:c AND enabled=true AND minute<=:m
        ORDER BY minute,player_off_id'''), {'c':club_id,'m':minute})).mappings().all()
    for existing in prior:
        # Never bring back a player who already left. Also reject an immediate
        # same-minute chain where the player just brought on is sent straight
        # back off; later-minute normal tactical rotation remains allowed.
        if int(existing['player_off_id'])==int(player_on_id):
            raise ValueError('A player who already came off cannot be brought back.')
        if int(existing['minute'])==int(minute) and int(existing['player_on_id'])==int(player_off_id):
            raise ValueError('A player just brought on cannot be sent off in the same minute.')
    await s.execute(text('INSERT INTO club_substitution_plans(club_id,minute,player_off_id,player_on_id) VALUES(:c,:m,:off,:on) ON CONFLICT(club_id,minute,player_off_id) DO UPDATE SET player_on_id=EXCLUDED.player_on_id,enabled=true'),{'c':club_id,'m':minute,'off':player_off_id,'on':player_on_id})

async def _substitution_plans(s, club_id):
    return (await s.execute(text('SELECT minute,player_off_id,player_on_id FROM club_substitution_plans WHERE club_id=:c AND enabled=true ORDER BY minute'),{'c':club_id})).mappings().all()

async def _club_tactics(s, club_id):
    row=(await s.execute(text('SELECT * FROM club_tactics WHERE club_id=:c'),{'c':club_id})).mappings().first()
    if not row:
        await s.execute(text('INSERT INTO club_tactics(club_id) VALUES(:c) ON CONFLICT DO NOTHING'),{'c':club_id})
        row=(await s.execute(text('SELECT * FROM club_tactics WHERE club_id=:c'),{'c':club_id})).mappings().first()
    return _tactics_from_row(row)

async def _save_lineup_and_tactics(s, match_id, club_id, players, tactics):
    roles=[slot for slot,_,_ in FORMATION_SLOTS[tactics.formation]]
    for p,role in zip(players,roles):
        await s.execute(text('''INSERT INTO match_lineups(match_id,club_id,player_id,position,role,is_starting)
            VALUES(:m,:c,:p,:pos,:role,true)'''),{'m':match_id,'c':club_id,'p':p.id,'pos':p.position,'role':role})
    await s.execute(text('''INSERT INTO match_tactics(match_id,club_id,formation,style,tempo,pressing,width,defensive_line,aggression)
        VALUES(:m,:c,:f,:s,:t,:p,:w,:d,:a)'''),
        {'m':match_id,'c':club_id,'f':tactics.formation,'s':tactics.style.value,'t':tactics.tempo,'p':tactics.pressing,
         'w':tactics.width,'d':tactics.defensive_line,'a':tactics.aggression})


async def _club_board(s, club_id, players=None, formation=None):
    from app.game.board import TacticalBoard
    if formation is None:
        formation=(await _club_tactics(s,club_id)).formation

    saved=(await s.execute(
        text('SELECT player_ids FROM club_lineups WHERE club_id=:c'),
        {'c':club_id}
    )).scalar_one_or_none()

    # club_lineups.player_ids is the authoritative XI/slot order. A lineup
    # is considered valid only when all eleven saved players are currently
    # available in this club. This prevents stale/injured/deleted IDs from
    # shifting the board to a different squad.
    if not saved or len(saved) != 11 or len(set(map(int,saved))) != 11:
        players=await _starting_players(s,club_id)
        saved=[int(p.id) for p in players]
    else:
        saved=[int(pid) for pid in saved]
        rows=(await s.execute(text(
            f"SELECT {PLAYER_FIELDS},cp.fitness,cp.form,cp.morale,cp.is_injured,cp.injury_matches,cp.suspension_matches,p.secondary_positions"
            " FROM club_players cp JOIN players p ON p.id=cp.player_id"
            " WHERE cp.club_id=:c AND p.id=ANY(:ids)"
        ), {'c':club_id,'ids':saved})).mappings().all()
        by_id={int(r['id']):row_player(r) for r in rows}
        if len(by_id) != 11 or any(by_id[pid].suspended for pid in saved if pid in by_id):
            players=await _starting_players(s,club_id)
            saved=[int(p.id) for p in players]
        else:
            # Rebuild from saved IDs, never from OVR/shirt-number order.
            players=[by_id[pid] for pid in saved]

    rows=(await s.execute(text(
        "SELECT player_id,role,instruction,board_x,board_y "
        "FROM club_player_instructions WHERE club_id=:c"
    ), {"c":club_id})).mappings().all()
    by={int(r['player_id']):r for r in rows}
    board=TacticalBoard(formation)

    # Index == formation slot. Never sort by OVR/shirt number here.
    for index,pid in enumerate(saved[:11]):
        p=next((player for player in players if int(player.id)==pid),None)
        if p is None:
            continue
        r=by.get(pid)
        if r:
            board=board.assign(index,r['role'],r['instruction'])
            if r['board_x'] is not None and r['board_y'] is not None:
                board=board.reposition(index,int(r['board_x']),int(r['board_y']))
    return board

async def set_player_instruction(s, club_id, player_id, role, instruction, x=50, y=50):
    from app.game.board import VALID_INSTRUCTIONS
    if instruction not in VALID_INSTRUCTIONS: raise ValueError("Unsupported instruction.")
    if not 5<=x<=95 or not 5<=y<=95: raise ValueError("Board coordinates must be 5..95.")
    exists=(await s.execute(text("SELECT 1 FROM club_players WHERE club_id=:c AND player_id=:p"),{"c":club_id,"p":player_id})).first()
    if not exists: raise ValueError("Игрок не входит в ваш состав.")
    await s.execute(text("""INSERT INTO club_player_instructions(club_id,player_id,role,instruction,board_x,board_y)
        VALUES(:c,:p,:r,:i,:x,:y) ON CONFLICT(club_id,player_id) DO UPDATE SET role=EXCLUDED.role,instruction=EXCLUDED.instruction,board_x=EXCLUDED.board_x,board_y=EXCLUDED.board_y"""),
        {"c":club_id,"p":player_id,"r":role,"i":instruction,"x":x,"y":y})

async def get_tactical_board(s, club_id):
    tactics=await _club_tactics(s,club_id)
    players=await _starting_players(s,club_id)
    player_ids=[int(p.id) for p in players]
    if not player_ids:
        return []
    rows=(await s.execute(text("""SELECT p.id,p.first_name,p.last_name,p.position,cp.shirt_number,
        COALESCE(i.role,'STARTER') role,COALESCE(i.instruction,'BALANCED') instruction,
        i.board_x board_x,i.board_y board_y
        FROM club_players cp JOIN players p ON p.id=cp.player_id
        LEFT JOIN club_player_instructions i ON i.club_id=cp.club_id AND i.player_id=cp.player_id
        WHERE cp.club_id=:c AND p.id=ANY(:ids)"""),{'c':club_id,'ids':player_ids})).mappings().all()
    by_id={int(r['id']):dict(r) for r in rows}
    slots=FORMATION_SLOTS[tactics.formation]
    counts=defaultdict(int)
    total_by_slot=defaultdict(int)
    for slot,_,_ in slots:
        total_by_slot[slot]+=1
    out=[]
    for index,p in enumerate(players[:11]):
        r=by_id.get(int(p.id))
        if r is None:
            continue
        slot,x,y=slots[index]
        counts[slot]+=1
        suffix=''
        if total_by_slot[slot]>1:
            suffix={1:'₁',2:'₂',3:'₃',4:'₄'}[counts[slot]]
        r['slot_index']=index
        r['slot_position']=slot
        r['slot_label']=POSITION_LABELS_RU.get(slot,slot)+suffix
        r['board_x']=x if r.get('board_x') is None else r['board_x']
        r['board_y']=y if r.get('board_y') is None else r['board_y']
        out.append(r)
    return out


async def _ensure_same_league(s, club_id, opponent_club_id):
    if club_id == opponent_club_id:
        raise ValueError('Opponent must be another club.')
    ok=(await s.execute(text('''SELECT 1
        FROM league_teams mine
        JOIN league_teams opp ON opp.league_id=mine.league_id
        WHERE mine.club_id=:c AND opp.club_id=:o
        LIMIT 1'''), {'c':club_id,'o':opponent_club_id})).first()
    if not ok:
        raise ValueError('Opponent is not in one of your leagues.')


async def get_counterplan(s, club_id, opponent_club_id, is_friendly: bool = False):
    """Return a saved counter-plan; isolated friendly/test matches need no league validation."""
    if is_friendly:
        return {'target_player_id': None, 'focus': 'BALANCED', 'intensity': 50,
                'formation': '4-3-3', 'style': 'BALANCE'}
    await _ensure_same_league(s, club_id, opponent_club_id)
    row=(await s.execute(text("SELECT target_player_id,focus,intensity FROM club_counterplans WHERE club_id=:c AND opponent_club_id=:o"),{'c':club_id,'o':opponent_club_id})).mappings().first()
    return dict(row) if row else {'target_player_id':None,'focus':'BALANCED','intensity':50}

async def set_counterplan(s, club_id, opponent_club_id, focus='BALANCED', intensity=50, target_player_id=None):
    await _ensure_same_league(s, club_id, opponent_club_id)
    valid={'BALANCED','PRESS_PLAYMAKER','ATTACK_FLANKS','HIGH_LINE_TRAP','TARGET_SLOW_CB','LOW_BLOCK'}
    focus=focus.upper()
    if focus not in valid: raise ValueError('Unknown counter-plan.')
    if not 0<=intensity<=100: raise ValueError('Counter-plan intensity must be 0..100.')
    if target_player_id is not None:
        ok=(await s.execute(text('SELECT 1 FROM club_players WHERE club_id=:c AND player_id=:p'),{'c':opponent_club_id,'p':target_player_id})).first()
        if not ok: raise ValueError('Target player does not belong to the opponent.')
    await s.execute(text('''INSERT INTO club_counterplans(club_id,opponent_club_id,target_player_id,focus,intensity)
        VALUES(:c,:o,:p,:f,:i) ON CONFLICT(club_id,opponent_club_id) DO UPDATE SET target_player_id=EXCLUDED.target_player_id,focus=EXCLUDED.focus,intensity=EXCLUDED.intensity'''),
        {'c':club_id,'o':opponent_club_id,'p':target_player_id,'f':focus,'i':intensity})
    return {'target_player_id':target_player_id,'focus':focus,'intensity':intensity}

async def scout_opponent(s, club_id, opponent_club_id, limit=5):
    await _ensure_same_league(s, club_id, opponent_club_id)
    from app.game.scouting import build_scout_report
    opponent=(await s.execute(text('SELECT id,name FROM clubs WHERE id=:c'),{'c':opponent_club_id})).mappings().first()
    if not opponent: raise ValueError('Opponent club not found.')
    rows=(await s.execute(text('''SELECT m.id,m.home_club_id,m.away_club_id,m.home_score,m.away_score,
            mt.formation,mt.style,mt.tempo,mt.pressing,mt.width,mt.defensive_line,mt.aggression
        FROM matches m JOIN match_tactics mt ON mt.match_id=m.id AND mt.club_id=:opp
        WHERE m.status='FINISHED' AND (m.home_club_id=:opp OR m.away_club_id=:opp)
        ORDER BY m.id DESC LIMIT :lim'''),{'opp':opponent_club_id,'lim':limit})).mappings().all()
    matches=[]; match_ids=[]
    for r in rows:
        gf=r['home_score'] if r['home_club_id']==opponent_club_id else r['away_score']
        ga=r['away_score'] if r['home_club_id']==opponent_club_id else r['home_score']
        matches.append({'formation':r['formation'],'style':r['style'],'goals_for':gf,'goals_against':ga})
        match_ids.append(r['id'])
    events={}
    if match_ids:
        ev=(await s.execute(text('''SELECT p.first_name,p.last_name,count(*) n FROM match_events e JOIN players p ON p.id=e.player_id
            WHERE e.club_id=:c AND e.match_id=ANY(:ids) AND e.event_type IN ('GOAL','ASSIST')
            GROUP BY p.id,p.first_name,p.last_name ORDER BY n DESC,p.last_name LIMIT 10'''),{'c':opponent_club_id,'ids':match_ids})).mappings().all()
        events={display_player_name(r.get('first_name'),r.get('last_name')):r['n'] for r in ev}
    return build_scout_report(opponent_club_id,opponent['name'],matches,events)

async def next_opponent(s, club_id, league_id):
    return (await s.execute(text('''SELECT m.id,m.round,m.home_club_id,m.away_club_id,
        CASE WHEN m.home_club_id=:c THEN m.away_club_id ELSE m.home_club_id END opponent_id,
        c.name opponent_name
        FROM matches m JOIN clubs c ON c.id=CASE WHEN m.home_club_id=:c THEN m.away_club_id ELSE m.home_club_id END
        WHERE m.league_id=:l AND m.status='SCHEDULED' AND (m.home_club_id=:c OR m.away_club_id=:c)
        ORDER BY m.round,m.id LIMIT 1'''),{'c':club_id,'l':league_id})).mappings().first()


async def ensure_club_economy(s, club_id):
    await s.execute(text("UPDATE clubs SET ticket_price=COALESCE(ticket_price,20), sponsor_level=COALESCE(sponsor_level,1) WHERE id=:c"), {'c':club_id})


BANKRUPTCY_WARNING = '⚠️ У вас минус, срочно продайте игрока.'


def check_bankruptcy(club) -> bool:
    """Return True when a club is bankrupt and purchases must be blocked."""
    if club is None:
        return False
    if isinstance(club, dict):
        budget = club.get('budget', 0)
    else:
        try:
            budget = club['budget']
        except (TypeError, KeyError, IndexError):
            budget = getattr(club, 'budget', 0)
    return int(budget or 0) < 0


async def record_financial_transaction(s, club_id, category, amount, description, league_id=None, match_id=None, allow_debt=False):
    if match_id is not None:
        existing=(await s.execute(text('SELECT balance_after FROM club_financial_transactions WHERE match_id=:m AND club_id=:c AND category=:cat LIMIT 1'), {'m':match_id,'c':club_id,'cat':category})).scalar_one_or_none()
        if existing is not None:
            return int(existing)
    club=(await s.execute(text('SELECT budget,COALESCE(debt,0) debt FROM clubs WHERE id=:c FOR UPDATE'), {'c':club_id})).mappings().first()
    if not club: raise ValueError('Клуб не найден.')
    if int(amount) < 0 and check_bankruptcy(club):
        raise ValueError(BANKRUPTCY_WARNING)
    new_balance=int(club['budget'])+int(amount)
    new_debt=int(club['debt'])
    if new_balance<0:
        if not allow_debt:
            raise ValueError(f'{BANKRUPTCY_WARNING} Покупка отменена: недостаточно средств.')
        new_debt += -new_balance
        new_balance = 0
    await s.execute(text('UPDATE clubs SET budget=:b,debt=:d WHERE id=:c'), {'b':new_balance,'d':new_debt,'c':club_id})
    await s.execute(text("""INSERT INTO club_financial_transactions(club_id,league_id,match_id,category,amount,balance_after,description)
        VALUES(:c,:l,:m,:cat,:a,:b,:d)"""), {'c':club_id,'l':league_id,'m':match_id,'cat':category,'a':int(amount),'b':new_balance,'d':description})
    if league_id:
        await s.execute(text("""INSERT INTO club_season_finances(league_id,club_id) VALUES(:l,:c)
            ON CONFLICT(league_id,club_id) DO NOTHING"""), {'l':league_id,'c':club_id})
        column={'TICKETS':'ticket_revenue','SPONSOR':'sponsor_revenue','PRIZE':'prize_money','WAGES':'wages','STADIUM':'stadium_costs','TRANSFER_IN':'transfer_income','TRANSFER_OUT':'transfer_spend','OTHER':'other_income' if amount>=0 else 'other_expense'}[category]
        await s.execute(text(f'UPDATE club_season_finances SET {column}={column}+:v WHERE league_id=:l AND club_id=:c'), {'v':abs(int(amount)), 'l':league_id, 'c':club_id})
    return new_balance


async def settle_match_economy(s, league_id, match_id, home_club_id, away_club_id):
    existing=(await s.execute(text('SELECT 1 FROM club_financial_transactions WHERE match_id=:m LIMIT 1 FOR UPDATE'), {'m':match_id})).first()
    if existing:
        return {'already_settled': True}
    rows=(await s.execute(text('SELECT * FROM clubs WHERE id=ANY(:ids)'), {'ids':[home_club_id,away_club_id]})).mappings().all()
    clubs={r['id']:r for r in rows}
    if len(clubs)!=2: raise ValueError('Both clubs must exist for financial settlement.')
    home=calculate_match_economy(clubs[home_club_id], clubs[away_club_id], home=True)
    season_row=(await s.execute(text('SELECT season_id FROM leagues WHERE id=:l'),{'l':league_id})).mappings().first()
    cfg=await get_liveops_config(s, season_row['season_id'] if season_row else None)
    vip_level=int(clubs[home_club_id].get('vip_level',1) or 1)
    ticket_revenue=int(round(home.ticket_revenue*ticket_income_multiplier(vip_level)*multiplier(cfg,'match_ticket_multiplier')))
    await record_financial_transaction(s,home_club_id,'TICKETS',ticket_revenue,
        f'Dомашний матч: {home.attendance:,} зрителей × €{home.ticket_price}',league_id,match_id)
    await record_financial_transaction(s,home_club_id,'STADIUM',-home.stadium_cost,
        f'Содержание стадиона и матчдэй: {home.attendance:,} зрителей',league_id,match_id,True)
    for cid in (home_club_id,away_club_id):
        c=clubs[cid]
        sponsor=sponsor_income(c['sponsor_level'],float(c['reputation'] or 0))
        sponsor=int(round(sponsor*multiplier(cfg,'sponsor_multiplier')))
        await record_financial_transaction(s,cid,'SPONSOR',sponsor,'Спонсорский доход за тур',league_id,match_id)
        salaries=(await s.execute(text('SELECT COALESCE(cp.contract_salary,p.salary) FROM club_players cp JOIN players p ON p.id=cp.player_id WHERE cp.club_id=:c'),{'c':cid})).scalars().all()
        wages=int(round(wage_bill(salaries)*multiplier(cfg,'wage_multiplier')))
        await record_financial_transaction(s,cid,'WAGES',-wages,f'Зарплатная ведомость: €{wages:,}',league_id,match_id,True)
        balance=(await s.execute(text('SELECT budget,debt FROM clubs WHERE id=:c'),{'c':cid})).mappings().first()
        if int(balance['budget'])<1_000_000 or int(balance['debt'])>0:
            await queue_club_notification(s,cid,'FINANCE','⚠️ Критический баланс',
                f'В казне осталось €{int(balance["budget"]):,}. Долг: €{int(balance["debt"]):,}. Проверьте финансы клуба.',
                f'low-budget:{league_id}:{match_id}:{cid}')
    return {'home':home,'away':calculate_match_economy(clubs[away_club_id],clubs[home_club_id],home=False)}


async def set_ticket_price(s, club_id, price):
    price=int(price)
    if not TICKET_MIN<=price<=TICKET_MAX: raise ValueError(f'Цена билета должна быть €{TICKET_MIN}..€{TICKET_MAX}.')
    await s.execute(text('UPDATE clubs SET ticket_price=:p WHERE id=:c'), {'p':price,'c':club_id})
    return price


async def upgrade_stadium(s, club_id):
    club=(await s.execute(text('SELECT stadium_level,budget FROM clubs WHERE id=:c FOR UPDATE'),{'c':club_id})).mappings().first()
    if not club: raise ValueError('Клуб не найден.')
    level=int(club['stadium_level'])
    if level>=20: raise ValueError('Stadium is already at maximum level.')
    cost=1_000_000*level
    await record_financial_transaction(s,club_id,'STADIUM',-cost,f'Mодернизация стадиона до LVL {level+1}')
    await s.execute(text('UPDATE clubs SET stadium_level=stadium_level+1 WHERE id=:c'),{'c':club_id})
    return level+1,cost,stadium_capacity(level+1)


async def financial_report(s, club_id, league_id=None, limit=12):
    if league_id:
        rows=(await s.execute(text('''SELECT category,SUM(amount) total,COUNT(*) n FROM club_financial_transactions
            WHERE club_id=:c AND league_id=:l GROUP BY category ORDER BY category'''),{'c':club_id,'l':league_id})).mappings().all()
    else:
        rows=(await s.execute(text('''SELECT category,SUM(amount) total,COUNT(*) n FROM club_financial_transactions
            WHERE club_id=:c GROUP BY category ORDER BY category'''),{'c':club_id})).mappings().all()
    recent=(await s.execute(text('''SELECT created_at,category,amount,balance_after,description FROM club_financial_transactions
        WHERE club_id=:c ORDER BY id DESC LIMIT :n'''),{'c':club_id,'n':limit})).mappings().all()
    return rows,recent


async def settle_season_prizes(s, league_id):
    season_number=int((await s.execute(text('SELECT season_number FROM leagues WHERE id=:l'), {'l':league_id})).scalar_one() or 1)
    marker=f'Призовые за % · сезон {season_number}'
    if (await s.execute(text("SELECT 1 FROM club_financial_transactions WHERE league_id=:l AND category='PRIZE' AND description LIKE :marker LIMIT 1"), {'l':league_id,'marker':marker})).first():
        return []
    rows=(await s.execute(text('''SELECT club_id,ROW_NUMBER() OVER (ORDER BY points DESC,(goals_for-goals_against) DESC,goals_for DESC,club_id) position,
        COUNT(*) OVER () teams FROM league_teams WHERE league_id=:l'''),{'l':league_id})).mappings().all()
    out=[]
    for r in rows:
        amount=prize_money(r['position'],r['teams'])
        await record_financial_transaction(s,r['club_id'],'PRIZE',amount,f'Призовые за {r["position"]}-е место · сезон {season_number}',league_id)
        out.append((r['club_id'],r['position'],amount))
    return out


async def fix_null_player_template_ids(s):
    """Backfill missing player template ids once; safe to run repeatedly."""
    missing=int((await s.execute(text('SELECT COUNT(*) FROM players WHERE template_id IS NULL'))).scalar_one())
    total=int((await s.execute(text('SELECT COUNT(*) FROM players'))).scalar_one())
    await s.execute(text('UPDATE players SET template_id=id WHERE template_id IS NULL'))
    return missing, total-missing


def _tactics_json(t):
    return {'formation':t.formation,'style':t.style.value,'tempo':t.tempo,'pressing':t.pressing,'width':t.width,'defensive_line':t.defensive_line,'aggression':t.aggression}

def _tactics_from_json(d):
    return Tactics(formation=d.get('formation','4-3-3'), style=Style(d.get('style','BALANCE')), tempo=int(d.get('tempo',50)), pressing=int(d.get('pressing',50)), width=int(d.get('width',50)), defensive_line=int(d.get('defensive_line',50)), aggression=int(d.get('aggression',50)))

async def schedule_round(s, league_id, round_no, delay_minutes=10):
    league=(await s.execute(text('SELECT id,current_round,status FROM leagues WHERE id=:l FOR UPDATE'),{'l':league_id})).mappings().first()
    if not league or league['status']!='ACTIVE': raise ValueError('Лига не активна.')
    if league['current_round']!=round_no: raise ValueError(f'Expected round {league["current_round"]}, got {round_no}.')
    ms=(await s.execute(text("SELECT id,home_club_id,away_club_id,status,scheduled_at FROM matches WHERE league_id=:l AND round=:r AND competition_type='LEAGUE' ORDER BY id FOR UPDATE"),{'l':league_id,'r':round_no})).mappings().all()
    if not ms: raise ValueError('В этом туре нет матчей.')
    if any(m['status']=='LIVE' for m in ms): raise ValueError('Этот тур уже идёт.')
    if any(m['status']=='FINISHED' for m in ms): raise ValueError('This round has already been completed.')
    scheduled_at=ms[0].get('scheduled_at') if 'scheduled_at' in ms[0] else None
    if scheduled_at is None:
        scheduled_at=datetime.now(timezone.utc)+timedelta(minutes=delay_minutes)
        await s.execute(text("UPDATE matches SET scheduled_at=CAST(:at AS timestamptz) WHERE league_id=:l AND round=:r AND status='SCHEDULED'"),{'at':scheduled_at,'l':league_id,'r':round_no})
    return scheduled_at,len(ms)

async def start_round_halftime(s, league_id, round_no):
    """Starts every scheduled match of a league round and stops at 45'."""
    from app.game.engine import simulate_first_half
    league=(await s.execute(text('SELECT id,current_round,total_rounds,status FROM leagues WHERE id=:l FOR UPDATE'),{'l':league_id})).mappings().first()
    if not league or league['status']!='ACTIVE': raise ValueError('Лига не активна.')
    if league['current_round'] != round_no:
        logger.warning(
            "Round mismatch: league=%s expected=%s got=%s",
            league_id, league['current_round'], round_no,
        )
        raise ValueError(f'Expected round {league["current_round"]}, got {round_no}.')
    ms=(await s.execute(text("SELECT id,home_club_id,away_club_id,seed,scheduled_at FROM matches WHERE league_id=:l AND round=:r AND competition_type='LEAGUE' AND status='SCHEDULED' ORDER BY id FOR UPDATE"),{'l':league_id,'r':round_no})).mappings().all()
    if not ms: raise ValueError('No scheduled matches in this round.')
    now=datetime.now(timezone.utc)
    # Once kickoff is reached, the pre-start reminder is stale and must not
    # arrive late if the worker had been offline or delayed.
    await s.execute(text('''UPDATE notifications SET cancelled_at=COALESCE(cancelled_at,now()),processing_at=NULL
        WHERE dedupe_key LIKE :prefix AND sent_at IS NULL AND failed_at IS NULL'''),
        {'prefix':f'match-start-10:{league_id}:{round_no}:%'})
    out=[]
    for m in ms:
        await train_squad(s,m['home_club_id'])
        await train_squad(s,m['away_club_id'])
        home=await _starting_players(s,m['home_club_id']); away=await _starting_players(s,m['away_club_id'])
        ht=await _club_tactics(s,m['home_club_id']); at=await _club_tactics(s,m['away_club_id'])
        hb=await _club_board(s,m['home_club_id'],home,ht.formation); ab=await _club_board(s,m['away_club_id'],away,at.formation)
        hc=await get_counterplan(s,m['home_club_id'],m['away_club_id']); ac=await get_counterplan(s,m['away_club_id'],m['home_club_id'])
        match_weather=generate_weather(__import__('random').Random(int(m['seed'])*1009+1))
        hs,as_,events=simulate_first_half(home,away,ht,at,m['seed']*2+1,hc,ac,hb,ab,match_weather)
        brilliance_ids=[int(e.player_id) for e in events if e.type=='MOMENT_OF_BRILLIANCE' and e.player_id is not None]
        kickoff=m.get('scheduled_at') or now
        phase_start=min(kickoff, now)
        full_hx,full_ax=expected_goals(home,away,ht,at,hb,ab,hc,ac,{'weather':match_weather})
        brilliance_bonus_home=sum(float((e.metadata or {}).get('xg_bonus',0.0)) for e in events if e.type=='MOMENT_OF_BRILLIANCE' and e.team=='HOME')
        brilliance_bonus_away=sum(float((e.metadata or {}).get('xg_bonus',0.0)) for e in events if e.type=='MOMENT_OF_BRILLIANCE' and e.team=='AWAY')
        full_hx += brilliance_bonus_home; full_ax += brilliance_bonus_away
        first_hx,first_ax=round(full_hx*.48,2),round(full_ax*.48,2)
        await s.execute(text("UPDATE matches SET home_score=0,away_score=0,home_xg=0,away_xg=0,home_possession=50,away_possession=50,home_halftime_score=:hs,away_halftime_score=:as_,current_minute=0,status='LIVE',halftime_locked=FALSE,halftime_data=:data,second_half_data='{}'::jsonb,live_phase='FIRST_HALF',phase_started_at=CAST(:phase_start AS timestamptz),halftime_ends_at=NULL,second_half_started_at=NULL,live_event_cursor=0,started_at=COALESCE(started_at,CAST(:phase_start AS timestamptz)),finished_at=NULL WHERE id=:m"),{'hs':hs,'as_':as_,'m':m['id'],'phase_start':phase_start,'data':json.dumps({'home_tactics':_tactics_json(ht),'away_tactics':_tactics_json(at),'events':[e.__dict__ for e in events], 'home_board':[(z.x,z.y,z.role,z.instruction) for z in hb.slots], 'away_board':[(z.x,z.y,z.role,z.instruction) for z in ab.slots], 'home_players':[p.id for p in home], 'away_players':[p.id for p in away], 'first_half_xg':[first_hx,first_ax], 'full_xg':[clamp_xg(full_hx),clamp_xg(full_ax)]})})
        await _save_lineup_and_tactics(s,m['id'],m['home_club_id'],home,ht); await _save_lineup_and_tactics(s,m['id'],m['away_club_id'],away,at)
        for cid in (m['home_club_id'],m['away_club_id']):
            await queue_club_notification(s,cid,'MATCH','▶️ Матч начался',
                'Стартовый свисток! Откройте live-просмотр матча.',
                f'match-started:{m["id"]}')
        # The engine generates the deterministic half up front, but future
        # events stay in match state until the live clock reaches their minute.
        # This prevents a finished-looking event burst at kickoff.
        out.append({'match_id':m['id'],'home':m['home_club_id'],'away':m['away_club_id'],'home_score':0,'away_score':0,'events':[]})
    return out

async def change_halftime_tactics(s, match_id, club_id, tactics):
    match=(await s.execute(text("SELECT id,status,home_club_id,away_club_id,home_second_tactics,away_second_tactics FROM matches WHERE id=:m FOR UPDATE"),{'m':match_id})).mappings().first()
    if not match or match['status']!='LIVE' or not match['halftime_locked']: raise ValueError('Match is not at halftime.')
    if club_id not in (match['home_club_id'],match['away_club_id']): raise ValueError('You are not playing this match.')
    col='home_second_tactics' if club_id==match['home_club_id'] else 'away_second_tactics'
    await s.execute(text(f"UPDATE matches SET {col}=:t WHERE id=:m"),{'t':json.dumps(_tactics_json(tactics)),'m':match_id})
    await s.execute(text('UPDATE club_tactics SET formation=:f,style=:s,tempo=:t,pressing=:p,width=:w,defensive_line=:d,aggression=:a WHERE club_id=:c'),{'f':tactics.formation,'s':tactics.style.value,'t':tactics.tempo,'p':tactics.pressing,'w':tactics.width,'d':tactics.defensive_line,'a':tactics.aggression,'c':club_id})




async def enrich_pending_live_events(s):
    """AI-enrich deferred second-half event descriptions without locking match rows.

    Call this only after the phase-A transaction commits. Every event is marked attempted
    even on provider failure, preserving its template description as a durable fallback.
    """
    rows=(await s.execute(text("""SELECT e.id,e.match_id,e.event_type,e.minute,e.description,e.metadata,e.player_id,
            c.name club_name,home.name home_name,away.name away_name,
            p.first_name,p.last_name,
            (SELECT count(*) FROM match_events g WHERE g.match_id=e.match_id AND g.event_type='GOAL'
                AND (g.minute<e.minute OR (g.minute=e.minute AND g.id<=e.id)) AND g.club_id=m.home_club_id) home_score,
            (SELECT count(*) FROM match_events g WHERE g.match_id=e.match_id AND g.event_type='GOAL'
                AND (g.minute<e.minute OR (g.minute=e.minute AND g.id<=e.id)) AND g.club_id=m.away_club_id) away_score
        FROM match_events e JOIN matches m ON m.id=e.match_id
        JOIN clubs c ON c.id=e.club_id JOIN clubs home ON home.id=m.home_club_id JOIN clubs away ON away.id=m.away_club_id
        LEFT JOIN players p ON p.id=e.player_id
        WHERE COALESCE((e.metadata->>'ai_pending')::boolean,FALSE)=TRUE
          AND COALESCE((e.metadata->>'ai_attempted')::boolean,FALSE)=FALSE
        ORDER BY e.id LIMIT 20"""))).mappings().all()
    if not rows:
        return 0
    from app.game.ai_commentator import generate_comment
    updated=0
    for event in rows:
        meta=dict(event.get('metadata') or {})
        ai_text=None
        try:
            context={'event_type':event['event_type'],'minute':int(event['minute']),
                'player_name':display_player_name(event.get('first_name'),event.get('last_name')) if event.get('first_name') or event.get('last_name') else 'Игрок',
                'club_name':event['club_name'],'opponent_name':event['away_name'] if event['club_name']==event['home_name'] else event['home_name'],
                'home_name':event['home_name'],'away_name':event['away_name'],
                'score':f"{int(event['home_score'] or 0)}:{int(event['away_score'] or 0)}",
                'match_id':int(event.get('match_id') or 0),'match_minute_total':int(event['minute'])}
            ai_text=await asyncio.to_thread(generate_comment,event['event_type'],context)
        except Exception:
            logger.exception('Deferred AI commentary failed; template retained: event_id=%s',event['id'])
        meta['ai_pending']=False; meta['ai_attempted']=True
        if ai_text and len(ai_text.strip())>=10:
            description=ai_text.strip()
            if int(event['minute'])>90 and not description.startswith(f"90+{int(event['minute'])-90}"):
                description=f"90+{int(event['minute'])-90}′ {description}"
            await s.execute(text('UPDATE match_events SET description=:d,metadata=CAST(:meta AS jsonb) WHERE id=:id'),
                {'d':description,'meta':json.dumps(meta,ensure_ascii=False),'id':event['id']})
        else:
            await s.execute(text('UPDATE match_events SET metadata=CAST(:meta AS jsonb) WHERE id=:id'),
                {'meta':json.dumps(meta,ensure_ascii=False),'id':event['id']})
        await s.commit()
        updated+=1
    return updated


# --- Real-time live match lifecycle ---
LIVE_SECONDS_PER_MINUTE = 5
LIVE_HALFTIME_SECONDS = 60
LIVE_FIRST_HALF_SECONDS = 45 * LIVE_SECONDS_PER_MINUTE
LIVE_SECOND_HALF_SECONDS = 45 * LIVE_SECONDS_PER_MINUTE
MAX_MATCH_MINUTES = 125

async def _live_match_rows(s, league_id=None):
    q="SELECT * FROM matches WHERE status='LIVE' AND UPPER(COALESCE(competition_type,'LEAGUE')) IN ('LEAGUE','FRIENDLY')"
    params={}
    if league_id is not None:
        q += " AND league_id=:l"; params['l']=league_id
    # Do not retain row locks across the whole per-match processing loop.
    # State transitions are guarded by conditional UPDATEs and transaction scope.
    q += " ORDER BY id LIMIT 25"
    return (await s.execute(text(q), params)).mappings().all()

def _dedupe_substitution_schedule(home_players, away_players, substitutions):
    """Reject substitution chains/cycles while preserving chronological order."""
    active={'HOME':{int(p.id) for p in home_players}, 'AWAY':{int(p.id) for p in away_players}}
    accepted=[]
    used={'HOME':0,'AWAY':0}
    same_minute_involved={'HOME':set(),'AWAY':set()}
    last_minute={'HOME':None,'AWAY':None}
    for item in sorted(substitutions or [], key=lambda x:(int(x[0]), str(x[3]), int(x[1]))):
        minute,off_id,on_player,team=item
        team=str(team).upper()
        if team not in active or used[team] >= MAX_SUBSTITUTIONS:
            continue
        if last_minute[team] != int(minute):
            same_minute_involved[team]=set(); last_minute[team]=int(minute)
        off_id=int(off_id); on_id=int(on_player.id)
        if off_id not in active[team] or on_id in active[team]:
            continue
        if off_id in same_minute_involved[team] or on_id in same_minute_involved[team]:
            continue
        active[team].remove(off_id)
        active[team].add(on_id)
        same_minute_involved[team].update((off_id,on_id))
        used[team]+=1
        accepted.append((int(minute),off_id,on_player,team))
    return accepted


def _projected_live_fatigue(players, minute):
    """Service-side fatigue estimate used to schedule bot auto-subs."""
    out={}
    elapsed=max(0,int(minute)-45)
    for p in players:
        stamina=max(1,int(getattr(p,'stamina',50) or 50))
        fitness=max(0,min(100,int(getattr(p,'fitness',100) or 100)))
        out[int(p.id)]=min(100.0,max(0.0,(100-fitness)+elapsed*2.0+max(0,60-stamina)*0.4))
    return out


async def _bot_club_ids(s, club_ids):
    rows=(await s.execute(text("""SELECT c.id,u.telegram_id FROM clubs c JOIN users u ON u.id=c.owner_user_id WHERE c.id=ANY(:ids)"""), {'ids':[int(x) for x in club_ids]})).mappings().all()
    return {int(r['id']) for r in rows if int(r['telegram_id'])<0}


async def _run_ai_transfer_cycle(s, club_ids):
    bot_ids=await _bot_club_ids(s, club_ids)
    actions=[]
    for club_id in sorted(bot_ids):
        club=(await s.execute(text('SELECT id,name,reputation,budget FROM clubs WHERE id=:c FOR UPDATE'), {'c':club_id})).mappings().first()
        if not club:
            continue
        action=await ai_transfer_action(dict(club),s)
        if not action:
            continue
        try:
            if action['action']=='BUY':
                player,price=await buy_player(s,club_id,action['player_id'])
                pname=display_player_name(player.get('first_name'),player.get('last_name'))
                logger.info('🤖 AI-клуб (%s) купил %s за €%s',club['name'],pname,price)
                actions.append({'club_id':club_id,'action':'BUY','player_id':action['player_id'],'price':price})
            elif action['action']=='SELL':
                row,price=await sell_player(s,club_id,action['player_id'])
                pname=display_player_name(row.get('first_name'),row.get('last_name'))
                logger.info('🤖 AI-клуб (%s) продал %s за €%s',club['name'],pname,price)
                actions.append({'club_id':club_id,'action':'SELL','player_id':action['player_id'],'price':price})
        except ValueError as exc:
            logger.info('AI transfer skipped for club=%s: %s',club_id,exc)
    return actions


async def _prepare_second_half_live(s, match, phase_start_at=None):
    from app.game.engine import simulate_second_half
    original_home=await _starting_players(s,match['home_club_id']); original_away=await _starting_players(s,match['away_club_id'])

    async def current_match_xi(club_id, fallback):
        """Use the match-specific XI, including live substitutions, at half-time."""
        ids=(await s.execute(text('''SELECT player_id FROM match_lineups
            WHERE match_id=:m AND club_id=:c AND is_starting=TRUE ORDER BY id'''),
            {'m':int(match['id']),'c':int(club_id)})).scalars().all()
        ids=[int(pid) for pid in ids]
        if len(ids)!=11 or len(set(ids))!=11:
            return fallback
        rows=(await s.execute(text(f'''SELECT {PLAYER_FIELDS},cp.fitness,cp.form,cp.morale,
                cp.is_injured,cp.injury_matches,cp.suspension_matches,p.secondary_positions
            FROM club_players cp JOIN players p ON p.id=cp.player_id
            WHERE cp.club_id=:c AND p.id=ANY(:ids)'''),
            {'c':int(club_id),'ids':ids})).mappings().all()
        by_id={int(row['id']):row_player(row) for row in rows}
        if len(by_id)!=11:
            return fallback
        return [by_id[pid] for pid in ids]

    # The club's saved XI may differ from the in-match XI after a LIVE
    # substitution. Keep the substitution match-scoped rather than mutating the
    # user's permanent club_lineups row.
    original_home=await current_match_xi(match['home_club_id'],original_home)
    original_away=await current_match_xi(match['away_club_id'],original_away)
    # First-half red cards and injuries affect the second-half active XI.
    unavailable=(await s.execute(text('''SELECT club_id,player_id,event_type,minute FROM match_events
        WHERE match_id=:m AND minute<=45 AND event_type IN ('RED','INJURY') AND player_id IS NOT NULL'''),
        {'m':match['id']})).mappings().all()
    red_by_club={match['home_club_id']:set(),match['away_club_id']:set()}
    injury_by_club={match['home_club_id']:set(),match['away_club_id']:set()}
    first_half_exits=[]
    for e in unavailable:
        target=red_by_club if e['event_type']=='RED' else injury_by_club
        target.setdefault(e['club_id'],set()).add(e['player_id'])
        first_half_exits.append((int(e['minute']),int(e['player_id']),e['event_type'],'HOME' if e['club_id']==match['home_club_id'] else 'AWAY'))
    unavailable_by_club=red_by_club
    # Keep the established exclusion shape for first-half incidents, then add
    # injured players back so the 46' bot auto-sub can replace them like-for-like.
    home=[p for p in original_home if p.id not in unavailable_by_club.get(match['home_club_id'],set())]
    away=[p for p in original_away if p.id not in unavailable_by_club.get(match['away_club_id'],set())]
    home.extend(p for p in original_home if p.id in injury_by_club.get(match['home_club_id'],set()))
    away.extend(p for p in original_away if p.id in injury_by_club.get(match['away_club_id'],set()))
    hrow=match['home_second_tactics'] or {}; arow=match['away_second_tactics'] or {}
    ht=_tactics_from_json(hrow) if hrow else await _club_tactics(s,match['home_club_id'])
    at=_tactics_from_json(arow) if arow else await _club_tactics(s,match['away_club_id'])
    hb=await _club_board(s,match['home_club_id'],home,ht.formation); ab=await _club_board(s,match['away_club_id'],away,at.formation)
    hplans=await _substitution_plans(s,match['home_club_id']); aplans=await _substitution_plans(s,match['away_club_id'])
    home_by_id={p.id:p for p in (home + await _bench_players(s,match['home_club_id'],[x.id for x in home]))}
    away_by_id={p.id:p for p in (away + await _bench_players(s,match['away_club_id'],[x.id for x in away]))}
    substitutions=[]
    for r in hplans:
        if r['player_off_id'] in home_by_id and r['player_on_id'] in home_by_id: substitutions.append((r['minute'],r['player_off_id'],home_by_id[r['player_on_id']],'HOME'))
    for r in aplans:
        if r['player_off_id'] in away_by_id and r['player_on_id'] in away_by_id: substitutions.append((r['minute'],r['player_off_id'],away_by_id[r['player_on_id']],'AWAY'))
    substitutions=_dedupe_substitution_schedule(home,away,substitutions)
    bot_ids=await _bot_club_ids(s,[match['home_club_id'],match['away_club_id']])
    for minute in (46,60,70,80):
        home_bench=await _bench_players(s,match['home_club_id'],[p.id for p in home]) if match['home_club_id'] in bot_ids else []
        away_bench=await _bench_players(s,match['away_club_id'],[p.id for p in away]) if match['away_club_id'] in bot_ids else []
        autos=auto_substitution(
            home,away,_projected_live_fatigue(home,minute),_projected_live_fatigue(away,minute),
            ht,hb,home_bench,away_bench,minute,
            injury_by_club.get(match['home_club_id'],set()),injury_by_club.get(match['away_club_id'],set()),
            red_by_club.get(match['home_club_id'],set()),red_by_club.get(match['away_club_id'],set()),
            sum(1 for x in substitutions if x[3]=='HOME'),sum(1 for x in substitutions if x[3]=='AWAY'))
        for auto in autos:
            if auto['team']=='HOME' and match['home_club_id'] not in bot_ids: continue
            if auto['team']=='AWAY' and match['away_club_id'] not in bot_ids: continue
            item=(minute,int(auto['player_out'].id),auto['player_in'],auto['team'])
            trial=_dedupe_substitution_schedule(home,away,substitutions+[item])
            if len(trial)>len(substitutions):
                substitutions=trial
                logger.info('🔄 Авто-замена: %s → %s (%s)',display_player_name(auto['player_out'].first_name,auto['player_out'].last_name),display_player_name(auto['player_in'].first_name,auto['player_in'].last_name),auto['reason'])
    is_friendly=str(match.get('competition_type') or '').upper()=='FRIENDLY'
    hc=await get_counterplan(s,match['home_club_id'],match['away_club_id'],is_friendly=is_friendly)
    ac=await get_counterplan(s,match['away_club_id'],match['home_club_id'],is_friendly=is_friendly)
    bot_rows=(await s.execute(text('SELECT c.id,u.telegram_id FROM clubs c JOIN users u ON u.id=c.owner_user_id WHERE c.id IN (:h,:a)'), {'h':match['home_club_id'],'a':match['away_club_id']})).mappings().all()
    bot_ids={int(r['id']) for r in bot_rows if int(r['telegram_id'])<0}
    halftime_data=match.get('halftime_data') or {}
    match_weather=halftime_data.get('weather') or generate_weather(__import__('random').Random(int(match['seed'])*1009+1))
    brilliance_ids=halftime_data.get('brilliance_ids') or []
    hs,as_,events,injury_time,extra_time_played=simulate_second_half(home,away,ht,at,match['seed']*2+2,substitutions,hc,ac,hb,ab,
        initial_home_score=int(match['home_halftime_score'] or 0),initial_away_score=int(match['away_halftime_score'] or 0),
        home_is_bot=match['home_club_id'] in bot_ids,away_is_bot=match['away_club_id'] in bot_ids,
        weather=match_weather,brilliance_excluded_ids=brilliance_ids,ai_commentary=False,
        return_injury_time=True,allow_extra_time=str(match.get('competition_type') or '').upper()=='CUP',return_extra_time=True)
    hname=(await s.execute(text('SELECT name FROM clubs WHERE id=:c'),{'c':match['home_club_id']})).scalar_one()
    aname=(await s.execute(text('SELECT name FROM clubs WHERE id=:c'),{'c':match['away_club_id']})).scalar_one()
    # As in the first half, keep the generated event timeline in phase state
    # and materialize only reached minutes in advance_live_matches().
    final_h=(match['home_halftime_score'] or 0)+hs; final_a=(match['away_halftime_score'] or 0)+as_
    data={'home':match['home_club_id'],'away':match['away_club_id'],'home_name':hname,'away_name':aname,'final_h':final_h,'final_a':final_a,
          'home_players':[p.id for p in original_home],'away_players':[p.id for p in original_away],
          'first_half_exits':first_half_exits,
          'substitutions':[(int(a),int(b),int(c.id),d) for a,b,c,d in substitutions],
          'events':[e.__dict__ for e in events],'injury_time':int(injury_time),'extra_time_played':bool(extra_time_played),'final_minute':90+int(injury_time)+(30 if extra_time_played else 0),'ai_deferred':True,
          'home_tactics':_tactics_json(ht),'away_tactics':_tactics_json(at),'weather':next((e.metadata.get('weather') for e in reversed(events) if e.type=='WEATHER' and e.metadata.get('weather')),match_weather)}
    final_home_tactics=ht; final_away_tactics=at
    for event in events:
        if event.type!='TACTICAL_CHANGE':
            continue
        meta=event.metadata or {}; sliders=meta.get('new_sliders') or {}
        target=Tactics(formation=meta.get('new_formation','4-3-3'),style=Style(meta.get('new_style','BALANCE')),
            tempo=int(sliders.get('tempo',50)),pressing=int(sliders.get('pressing',50)),width=int(sliders.get('width',50)),
            defensive_line=int(sliders.get('defensive_line',50)),aggression=int(sliders.get('aggression',50)))
        if event.team=='HOME': final_home_tactics=target
        elif event.team=='AWAY': final_away_tactics=target
    hx,ax=expected_goals(home,away,final_home_tactics,final_away_tactics)
    first_half_xg=[float(x) for x in (match.get('halftime_data') or {}).get('first_half_xg',[0,0])]
    adaptive_hx=next((float((e.metadata or {}).get('second_half_xg')) for e in reversed(events) if e.team=='HOME' and e.type=='TACTICAL_CHANGE' and (e.metadata or {}).get('second_half_xg') is not None),None)
    adaptive_ax=next((float((e.metadata or {}).get('second_half_xg')) for e in reversed(events) if e.team=='AWAY' and e.type=='TACTICAL_CHANGE' and (e.metadata or {}).get('second_half_xg') is not None),None)
    data['second_half_xg']=[round(adaptive_hx if adaptive_hx is not None else hx*.52,2),round(adaptive_ax if adaptive_ax is not None else ax*.52,2)]
    # Keep the existing full-match formula (data['full_xg']=[round(first_half_xg[0]+hx*.52,2), ...]) and only clamp its output.
    data['full_xg']=[clamp_xg(first_half_xg[0]+hx*.52),clamp_xg(first_half_xg[1]+ax*.52)]
    phase_start_at = phase_start_at or datetime.now(timezone.utc)
    await s.execute(text("""UPDATE matches SET current_minute=45,live_phase='SECOND_HALF',halftime_locked=FALSE,
        second_half_started_at=CAST(:start_at AS timestamptz),phase_started_at=CAST(:start_at AS timestamptz),halftime_ends_at=NULL,second_half_data=:data,
        home_xg=:hx,away_xg=:ax WHERE id=:m"""),
        {'m':match['id'],'hx':round(first_half_xg[0],2),'ax':round(first_half_xg[1],2),'data':json.dumps(data),'start_at':phase_start_at})
    return data

async def _materialize_live_events(s, match, phase_data, upto_minute):
    """Persist only events whose match minute has actually been reached."""
    if not phase_data or upto_minute <= 0:
        return 0
    events=phase_data.get('events') or []
    if not events:
        return 0
    existing=(await s.execute(text('''SELECT minute,event_type,club_id,player_id,secondary_player_id,description,metadata
        FROM match_events WHERE match_id=:m'''),{'m':match['id']})).mappings().all()
    # A set is not sufficient here: two legitimate events can have identical
    # fields (notably two goals in the same minute by the same player). Track
    # multiplicity so a later materialization pass is idempotent without
    # collapsing distinct events.
    from collections import Counter
    def event_key(minute, event_type, club_id, player_id, secondary_player_id, description):
        return (int(minute), event_type, club_id, player_id, secondary_player_id, description)
    existing_counts=Counter(event_key(r['minute'],r['event_type'],r['club_id'],r['player_id'],r['secondary_player_id'],(r.get('metadata') or {}).get('source_description') or r['description']) for r in existing)
    source_counts=Counter()
    inserted=0
    for raw in sorted(events,key=lambda e:(int(e.get('minute',0)),str(e.get('type','')),str(e.get('player','')))):
        minute=int(raw.get('minute',0))
        if minute<=0 or minute>int(upto_minute):
            continue
        team=raw.get('team')
        cid=match['home_club_id'] if team=='HOME' else match['away_club_id']
        # Player ids are already resolved in the phase snapshot when possible.
        # Fall back to the active XI by name for old snapshots.
        player_id=None; secondary_id=None
        player_id=raw.get('player_id')
        secondary_id=raw.get('secondary_player_id')
        player_id=int(player_id) if player_id is not None else None
        secondary_id=int(secondary_id) if secondary_id is not None else None
        description=sanitize_event_text(raw.get('text') or raw.get('description') or '')
        metadata=dict(raw.get('metadata') or {})
        if phase_data.get('ai_deferred') and raw.get('type') in {'GOAL','RED','INJURY','PENALTY'}:
            metadata['ai_pending']=True
            metadata['source_description']=description
        if raw.get('type')=='SUBSTITUTION':
            for sub_minute, sub_off, sub_on, sub_team in (phase_data.get('substitutions') or []):
                if int(sub_minute)==minute and int(sub_on)==int(player_id or -1) and sub_team==team:
                    names=(await s.execute(text('SELECT id,first_name,last_name FROM players WHERE id=ANY(:ids)'), {'ids':[int(sub_on),int(sub_off)]})).mappings().all()
                    by_id={int(r['id']):r for r in names}
                    if int(sub_on) in by_id and int(sub_off) in by_id:
                        description=f'⬆️ Вышел: {display_player_name(by_id[int(sub_on)]["first_name"],by_id[int(sub_on)]["last_name"])}, ⬇️ Ушёл: {display_player_name(by_id[int(sub_off)]["first_name"],by_id[int(sub_off)]["last_name"])}'
                    break
        # Normalize after any substitution-specific rewrite, immediately before DB insert.
        description=sanitize_event_text(description)
        key=event_key(minute,raw.get('type'),cid,player_id,secondary_id,description)
        source_counts[key] += 1
        if source_counts[key] <= existing_counts[key]:
            continue
        is_added_time=minute>90
        if is_added_time:
            metadata['is_added_time']=True
            metadata.setdefault('added_minute',minute-90)
        await s.execute(text('''INSERT INTO match_events(match_id,minute,event_type,club_id,player_id,secondary_player_id,description,metadata,is_added_time)
            VALUES(:m,:min,:t,:c,:p,:sp,:d,:meta,:added)'''),
            {'m':match['id'],'min':minute,'t':raw.get('type'),'c':cid,'p':player_id,'sp':secondary_id,'d':description,'meta':json.dumps(metadata),'added':is_added_time})
        existing_counts[key] += 1
        if raw.get('type')=='SHOT':
            on_target=str((metadata or {}).get('result','')).upper() in {'GOAL','SAVED'}
            await s.execute(text('''UPDATE matches SET home_shots=home_shots+:h,away_shots=away_shots+:a,home_shots_on_target=home_shots_on_target+:hot,away_shots_on_target=away_shots_on_target+:aot WHERE id=:m'''),
                {'h':1 if cid==match['home_club_id'] else 0,'a':1 if cid==match['away_club_id'] else 0,'hot':1 if cid==match['home_club_id'] and on_target else 0,'aot':1 if cid==match['away_club_id'] and on_target else 0,'m':match['id']})
        elif raw.get('type')=='CORNER':
            await s.execute(text('UPDATE matches SET home_corners=home_corners+:h,away_corners=away_corners+:a WHERE id=:m'),
                {'h':1 if cid==match['home_club_id'] else 0,'a':1 if cid==match['away_club_id'] else 0,'m':match['id']})
        inserted+=1
    return inserted


async def _live_score_at_minute(s, match_id, minute, home_club_id, away_club_id):
    if int(minute)>45:
        goal_window='minute>45 AND minute<=:minute'
    else:
        goal_window='minute<=:minute'
    rows=(await s.execute(text(f'''SELECT club_id,count(*) n FROM match_events
        WHERE match_id=:m AND event_type='GOAL' AND {goal_window} GROUP BY club_id'''),
        {'m':match_id,'minute':minute})).mappings().all()
    home=next((int(r['n']) for r in rows if r['club_id']==home_club_id),0)
    away=next((int(r['n']) for r in rows if r['club_id']==away_club_id),0)
    return home,away


def _live_possession(phase_data, minute):
    home_t=_tactics_from_json(phase_data.get('home_tactics') or {})
    away_t=_tactics_from_json(phase_data.get('away_tactics') or {})
    reached=[e for e in (phase_data.get('events') or []) if int(e.get('minute',0))<=int(minute)]
    for event in sorted((e for e in reached if e.get('type')=='TACTICAL_CHANGE'),key=lambda e:int(e.get('minute',0))):
        meta=event.get('metadata') or {}; sliders=meta.get('new_sliders') or {}
        target=Tactics(formation=meta.get('new_formation','4-3-3'),style=Style(meta.get('new_style','BALANCE')),
            tempo=int(sliders.get('tempo',50)),pressing=int(sliders.get('pressing',50)),width=int(sliders.get('width',50)),
            defensive_line=int(sliders.get('defensive_line',50)),aggression=int(sliders.get('aggression',50)))
        if event.get('team')=='HOME': home_t=target
        elif event.get('team')=='AWAY': away_t=target
    home_actions=sum(1 for e in reached if e.get('team')=='HOME' and e.get('type') in {'PASS','CARRY','INTERCEPTION'})
    away_actions=sum(1 for e in reached if e.get('team')=='AWAY' and e.get('type') in {'PASS','CARRY','INTERCEPTION'})
    tactical=(home_t.tempo-away_t.tempo)*0.06+(home_t.pressing-away_t.pressing)*0.035
    flow=(home_actions-away_actions)*0.35
    home=max(35.0,min(65.0,50.0+tactical+flow))
    return round(home,2),round(100.0-home,2)


async def _force_finish_live_match(s, match_id):
    """Best-effort terminal recovery; each recovery block is isolated by a savepoint."""
    row=(await s.execute(text('SELECT id,home_score,away_score,home_xg,away_xg,current_minute FROM matches WHERE id=:m'),{'m':match_id})).mappings().first()
    try:
        extra=(await s.execute(text('SELECT second_half_data,halftime_data,league_id,home_club_id,away_club_id,home_halftime_score,away_halftime_score,current_minute FROM matches WHERE id=:m FOR UPDATE'),{'m':match_id})).mappings().first()
    except Exception:
        extra=None
    if extra:
        row=dict(row); row.update(dict(extra))
    if not row: return None
    match=dict(row); data=match.get('second_half_data') or {}
    final_minute=max(0,int(match.get('current_minute') or 0))
    result={'match_id':match_id,'home_score':int(match.get('home_score') or 0),'away_score':int(match.get('away_score') or 0),'minute':final_minute}
    async def step(label,fn):
        try:
            if hasattr(s,'begin_nested'):
                async with s.begin_nested(): return await fn()
            return await fn()
        except Exception:
            logger.exception('Forced live-match recovery failed: match_id=%s step=%s',match_id,label); return None
    async def events():
        phase_data=data or (match.get('halftime_data') or {})
        await _materialize_live_events(s,match,phase_data,final_minute)
        await _ensure_foul_events(s,match_id,match['home_club_id'],match['away_club_id'],data.get('home_players',[]),data.get('away_players',[]))
    await step('events',events)
    async def score():
        h=int(match.get('home_score') or 0); a=int(match.get('away_score') or 0)
        if (data.get('home_players') or data.get('away_players')) and final_minute>45:
            try:
                sh,sa=await _live_score_at_minute(s,match_id,final_minute,match['home_club_id'],match['away_club_id']); h=int(match.get('home_halftime_score') or 0)+sh; a=int(match.get('away_halftime_score') or 0)+sa
            except Exception: logger.exception('Forced score reconstruction failed: match_id=%s',match_id)
        result['home_score']=h; result['away_score']=a
    await step('score',score)
    async def stats_motm():
        ids=set(data.get('home_players',[]))|set(data.get('away_players',[]))
        if not ids: return
        events=(await s.execute(text('SELECT minute,player_id,event_type FROM match_events WHERE match_id=:m ORDER BY minute,id'),{'m':match_id})).mappings().all()
        prs=(await s.execute(text(f'SELECT {PLAYER_FIELDS} FROM players p WHERE p.id=ANY(:ids)'),{'ids':list(ids)})).mappings().all()
        home_ids=set(data.get('home_players',[])); rows=[]; best=None; best_rating=-1
        for pr in prs:
            po=row_player(pr); goals=sum(1 for e in events if e['player_id']==po.id and e['event_type']=='GOAL'); assists=sum(1 for e in events if e['player_id']==po.id and e['event_type']=='ASSIST')
            rating=round(max(5,min(10,6.2+(player_rating(po)-65)*.055+goals*.85+assists*.45)),1); cid=match['home_club_id'] if po.id in home_ids else match['away_club_id']
            rows.append({'m':match_id,'c':cid,'p':po.id,'r':rating,'g':goals,'a':assists})
            if rating>best_rating: best_rating=rating; best=po.id
        if rows:
            await s.execute(text('''INSERT INTO match_player_stats(match_id,club_id,player_id,rating,minutes,goals,assists) VALUES(:m,:c,:p,:r,90,:g,:a)
                ON CONFLICT(match_id,player_id) DO UPDATE SET rating=EXCLUDED.rating,minutes=EXCLUDED.minutes,goals=EXCLUDED.goals,assists=EXCLUDED.assists'''),rows)
            await s.execute(text('UPDATE matches SET motm_player_id=:p WHERE id=:m'),{'p':best,'m':match_id})
    await step('stats_motm',stats_motm)
    async def side_effects():
        ids=(await s.execute(text('SELECT player_id FROM club_players WHERE club_id=ANY(:clubs) ORDER BY club_id,player_id'),{'clubs':[match['home_club_id'],match['away_club_id']]})).scalars().all()
        if ids: await record_snapshots([int(pid) for pid in ids],s,note=f'match:{match_id}:forced')
        await apply_match_reputation(s,match['home_club_id'],match['away_club_id'],result['home_score'],result['away_score'])
        from app.game.battle_pass import award_match_points
        await award_match_points(s,match['home_club_id'],match['away_club_id'],result['home_score'],result['away_score'])
        from app.game.inventory import advance_morale_boosts
        await advance_morale_boosts(s,[match['home_club_id'],match['away_club_id']])
    await step('snapshots_reputation',side_effects)
    async def economy():
        if match.get('league_id') is not None:
            await settle_match_economy(s,match['league_id'],match_id,match['home_club_id'],match['away_club_id'])
    await step('economy',economy)
    async def terminal():
        await s.execute(text('''UPDATE matches SET home_score=:h,away_score=:a,home_xg=:hx,away_xg=:ax,current_minute=:minute,status='FINISHED',live_phase='FINISHED',halftime_locked=FALSE,finished_at=COALESCE(finished_at,now()),forced_finish=TRUE WHERE id=:m AND status='LIVE' ''') ,{'m':match_id,'h':result['home_score'],'a':result['away_score'],'minute':final_minute,'hx':clamp_xg(match.get('home_xg') or 0),'ax':clamp_xg(match.get('away_xg') or 0)})
    await step('terminal_status',terminal)
    return result


async def _finish_live_match_emergency(s, match):
    """Finalize a timed-out live match using its reached score/minute, even without second-half data.

    League standings are updated by finalize_live_round() after this result is returned;
    the recovery pipeline handles BP points, reputation, snapshots, economy and terminal status.
    """
    match_id=int(match['id'])
    result=await _force_finish_live_match(s,match_id)
    if result:
        logger.error('emergency_finish match_id=%s score=%s:%s minute=%s',
            match_id,result['home_score'],result['away_score'],result.get('minute',match.get('current_minute',0)))
    return result


async def _finish_live_match(s, match):
    data=match['second_half_data'] or {}
    if not data: raise ValueError('Second-half state is missing.')
    final_minute=int(data.get('final_minute') or (90+max(0,min(5,int(data.get('injury_time') or 0)))))
    second_h,second_a=await _live_score_at_minute(s,match['id'],final_minute,match['home_club_id'],match['away_club_id'])
    final_h=int(match['home_halftime_score'] or 0)+second_h
    final_a=int(match['away_halftime_score'] or 0)+second_a
    home_pen=away_pen=None
    if str(match.get('competition_type') or '').upper()=='CUP' and data.get('extra_time_played') and final_h==final_a:
        from app.game.cup import shoot_penalties
        home_pen,away_pen=await shoot_penalties(s,dict(match))
    for cid,ids in ((match['home_club_id'],data.get('home_players',[])),(match['away_club_id'],data.get('away_players',[]))):
        if ids:
            result='WIN' if (cid==match['home_club_id'] and final_h>final_a) or (cid==match['away_club_id'] and final_a>final_h) else ('LOSS' if (cid==match['home_club_id'] and final_h<final_a) or (cid==match['away_club_id'] and final_a<final_h) else 'DRAW')
            morale_delta=2 if result=='WIN' else (-2 if result=='LOSS' else 0)
            await s.execute(text("""UPDATE club_players SET fitness=GREATEST(0,fitness-4),form=LEAST(2,form+1),
                morale=GREATEST(0,LEAST(100,morale+:m)),appearances=appearances+1,starts=starts+1,
                minutes_played=minutes_played+90 WHERE club_id=:c AND player_id=ANY(:ids)"""),{'c':cid,'ids':ids,'m':morale_delta})
        for minute,off_id,on_id,team in data.get('substitutions',[]):
            if team != ('HOME' if cid==match['home_club_id'] else 'AWAY'): continue
            played=max(0,90-int(minute))
            delta=1 if final_h>final_a and cid==match['home_club_id'] or final_a>final_h and cid==match['away_club_id'] else (-1 if final_h<final_a and cid==match['home_club_id'] or final_a<final_h and cid==match['away_club_id'] else 0)
            await s.execute(text("""UPDATE club_players SET appearances=appearances+1,minutes_played=minutes_played+:mins,
                fitness=GREATEST(0,fitness-:fatigue),morale=GREATEST(0,LEAST(100,morale+:m)) WHERE club_id=:c AND player_id=:p"""),{'mins':played,'fatigue':max(1,played//22),'m':delta,'c':cid,'p':on_id})
            await s.execute(text('UPDATE club_players SET minutes_played=GREATEST(0,minutes_played-:mins) WHERE club_id=:c AND player_id=:p'),{'mins':played,'c':cid,'p':off_id})
        for exit_minute,exit_player_id,exit_type,exit_team in data.get('first_half_exits',[]):
            if exit_team != ('HOME' if cid==match['home_club_id'] else 'AWAY'): continue
            played=max(0,min(45,int(exit_minute)))
            await s.execute(text('UPDATE club_players SET minutes_played=GREATEST(0,minutes_played-(90-:mins)),fitness=GREATEST(0,fitness-1) WHERE club_id=:c AND player_id=:p'),
                {'mins':played,'c':cid,'p':exit_player_id})
        recovering=(await s.execute(text('''SELECT player_id,injury_matches,suspension_matches,first_name,last_name FROM club_players cp
            JOIN players p ON p.id=cp.player_id WHERE cp.club_id=:c AND (injury_matches=1 OR suspension_matches=1)'''), {'c':cid})).mappings().all()
        await s.execute(text('UPDATE club_players SET injury_matches=GREATEST(0,injury_matches-1),suspension_matches=GREATEST(0,suspension_matches-1),is_injured=CASE WHEN injury_matches-1<=0 THEN false ELSE is_injured END WHERE club_id=:c'),{'c':cid})
        for rr in recovering:
            if int(rr['injury_matches'] or 0)==1:
                await queue_club_notification(s,cid,'INJURY','💚 Игрок восстановился',
                    f"{display_player_name(rr.get('first_name'),rr.get('last_name'))} полностью восстановился и снова доступен.",
                    f'injury-recovered:{cid}:{rr["player_id"]}:{match["id"]}')
            if int(rr['suspension_matches'] or 0)==1:
                await queue_club_notification(s,cid,'DISCIPLINE','✅ Дисквалификация закончилась',
                    f"{display_player_name(rr.get('first_name'),rr.get('last_name'))} снова доступен после дисквалификации.",
                    f'suspension-ended:{cid}:{rr["player_id"]}:{match["id"]}')
    discipline=(await s.execute(text("SELECT minute,club_id,player_id,event_type FROM match_events WHERE match_id=:m AND event_type IN ('INJURY','RED') AND player_id IS NOT NULL"),{'m':match['id']})).mappings().all()
    for e in discipline:
        pname_row=(await s.execute(text("SELECT first_name,last_name FROM players WHERE id=:p"),{'p':e['player_id']})).mappings().first()
        pname=display_player_name(pname_row.get('first_name'),pname_row.get('last_name')) if pname_row else 'Игрок'
        if e['event_type']=='INJURY':
            medical=int((await s.execute(text('SELECT medical_level FROM clubs WHERE id=:c'),{'c':e['club_id']})).scalar_one_or_none() or 1)
            staff_medical=int((await s.execute(text('SELECT staff_medical FROM clubs WHERE id=:c'),{'c':e['club_id']})).scalar_one_or_none() or 1)
            recovery_matches=max(1,int(round(2*recovery_multiplier(medical)*staff_bonus('medical',staff_medical))))
            await s.execute(text('UPDATE club_players SET is_injured=true,injury_matches=GREATEST(injury_matches,:im) WHERE club_id=:c AND player_id=:p'),{'c':e['club_id'],'p':e['player_id'],'im':recovery_matches})
            await queue_club_notification(s,e['club_id'],'INJURY','🩹 Игрок получил травму',f'{pname} получил травму. Ожидаемое восстановление — минимум 2 матча.',f'injury:{match["id"]}:{e["player_id"]}')
            await inbox_send_message(e['club_id'],'🩹 Травма игрока',f'{pname} получил травму. Ожидаемое восстановление — {recovery_matches} матч(а/ей).',s)
        elif e['event_type']=='RED':
            club_name=(await s.execute(text('SELECT name FROM clubs WHERE id=:c'),{'c':e['club_id']})).scalar_one_or_none() or 'Команда'
            await s.execute(text('UPDATE club_players SET suspension_matches=GREATEST(suspension_matches,1) WHERE club_id=:c AND player_id=:p'),{'c':e['club_id'],'p':e['player_id']})
            await queue_club_notification(s,e['club_id'],'DISCIPLINE','🟥 КРАСНАЯ',f'🟥 КРАСНАЯ: {pname} ({club_name}) ({int(e.get("minute") or 0)}′)',f'red:{match["id"]}:{e["player_id"]}')
    # Persist live player ratings/MOTM inputs before the match becomes FINISHED.
    events=(await s.execute(text('SELECT e.minute,e.player_id,e.secondary_player_id,e.event_type FROM match_events e WHERE e.match_id=:m ORDER BY e.minute,e.id'), {'m':match['id']})).mappings().all()
    home_ids=set(data.get('home_players',[])); away_ids=set(data.get('away_players',[]))
    for minute,off_id,on_id,team in data.get('substitutions',[]):
        (home_ids if team=='HOME' else away_ids).add(int(on_id))
    player_ids=home_ids | away_ids
    motm_player_id=None
    motm_rating=-1.0
    if player_ids:
        prs=(await s.execute(text(f"SELECT {PLAYER_FIELDS} FROM players p WHERE p.id=ANY(:ids)"), {'ids':list(player_ids)})).mappings().all()
        for pr in prs:
            p_obj=row_player(pr)
            goals=sum(1 for e in events if e['player_id']==p_obj.id and e['event_type']=='GOAL')
            assists=sum(1 for e in events if e['player_id']==p_obj.id and e['event_type']=='ASSIST')
            rating=round(max(5,min(10,6.2+(player_rating(p_obj)-65)*.055+goals*.85+assists*.45)),1)
            if rating > motm_rating:
                motm_rating=rating; motm_player_id=p_obj.id
            cid=match['home_club_id'] if p_obj.id in home_ids else match['away_club_id']
            minutes=90
            for exit_minute,exit_player_id,_,_team in data.get('first_half_exits',[]):
                if int(exit_player_id)==p_obj.id:
                    minutes=min(minutes,max(0,int(exit_minute)))
            for sub_minute,off_id,on_id,_team in data.get('substitutions',[]):
                if int(on_id)==p_obj.id:
                    minutes=min(minutes,max(0,90-int(sub_minute)))
                elif int(off_id)==p_obj.id:
                    minutes=min(minutes,max(0,int(sub_minute)))
            for e in events:
                if e['player_id']==p_obj.id and e['event_type'] in {'RED','INJURY'}:
                    minutes=min(minutes,int(e['minute']))
            await s.execute(text('''INSERT INTO match_player_stats(match_id,club_id,player_id,rating,minutes,goals,assists)
                VALUES(:m,:c,:p,:r,:min,:g,:a)
                ON CONFLICT(match_id,player_id) DO UPDATE SET rating=EXCLUDED.rating,minutes=EXCLUDED.minutes,goals=EXCLUDED.goals,assists=EXCLUDED.assists'''),
                {'m':match['id'],'c':cid,'p':p_obj.id,'r':rating,'min':minutes,'g':goals,'a':assists})
    await _ensure_foul_events(s,match['id'],match['home_club_id'],match['away_club_id'],data.get('home_players',[]),data.get('away_players',[]))
    stat_rows=(await s.execute(text('''SELECT club_id,event_type,metadata FROM match_events WHERE match_id=:m AND event_type IN ('SHOT','CORNER')'''),{'m':match['id']})).mappings().all()
    home_shots=sum(1 for e in stat_rows if e['club_id']==match['home_club_id'] and e['event_type']=='SHOT')
    away_shots=sum(1 for e in stat_rows if e['club_id']==match['away_club_id'] and e['event_type']=='SHOT')
    home_shots_on_target=sum(1 for e in stat_rows if e['club_id']==match['home_club_id'] and e['event_type']=='SHOT' and str((e.get('metadata') or {}).get('result','')).upper() in {'GOAL','SAVED'})
    away_shots_on_target=sum(1 for e in stat_rows if e['club_id']==match['away_club_id'] and e['event_type']=='SHOT' and str((e.get('metadata') or {}).get('result','')).upper() in {'GOAL','SAVED'})
    home_corners=sum(1 for e in stat_rows if e['club_id']==match['home_club_id'] and e['event_type']=='CORNER')
    away_corners=sum(1 for e in stat_rows if e['club_id']==match['away_club_id'] and e['event_type']=='CORNER')
    final_xg_home=clamp_xg((match.get('home_xg') or 0))
    final_xg_away=clamp_xg((match.get('away_xg') or 0))
    await s.execute(text("UPDATE matches SET home_score=:h,away_score=:a,home_penalties=:hp,away_penalties=:ap,home_xg=:hx,away_xg=:ax,home_shots=:hsht,away_shots=:asht,home_shots_on_target=:hsot,away_shots_on_target=:asot,home_corners=:hc,away_corners=:ac,current_minute=:minute,motm_player_id=:motm,status='FINISHED',live_phase='FINISHED',halftime_locked=FALSE,finished_at=now() WHERE id=:m"),
        {'h':final_h,'a':final_a,'hp':home_pen,'ap':away_pen,'hx':final_xg_home,'ax':final_xg_away,'hsht':home_shots,'asht':away_shots,'hsot':home_shots_on_target,'asot':away_shots_on_target,'hc':home_corners,'ac':away_corners,'motm':motm_player_id,'minute':final_minute,'m':match['id']})
    if str(match.get('competition_type') or '').upper()=='CUP':
        cup_match=(await s.execute(text('SELECT id,cup_id FROM cup_matches WHERE match_id=:m FOR UPDATE'),{'m':match['id']})).mappings().first()
        if cup_match:
            if final_h>final_a: winner_id=int(match['home_club_id'])
            elif final_a>final_h: winner_id=int(match['away_club_id'])
            elif home_pen is not None and away_pen is not None: winner_id=int(match['home_club_id'] if home_pen>away_pen else match['away_club_id'])
            else: winner_id=None
            if winner_id is None:
                raise ValueError(f'CUP match {match["id"]} tied after extra time without penalty result')
            await s.execute(text("UPDATE cup_matches SET home_score=:h,away_score=:a,home_penalties=:hp,away_penalties=:ap,winner_id=:w,status='FINISHED' WHERE id=:cm"),
                {'h':final_h,'a':final_a,'hp':home_pen,'ap':away_pen,'w':winner_id,'cm':cup_match['id']})
            from app.game.cup import advance_cup
            await advance_cup(s,int(cup_match['cup_id']))
    # One compact final notification per owner. Goal details and MOTM are kept
    # here so the delivery layer can attach a single full-report button.
    club_names={match['home_club_id']:data.get('home_name','Home'),match['away_club_id']:data.get('away_name','Away')}
    goal_events=(await s.execute(text('''SELECT e.minute,e.metadata,e.club_id,e.player_id,p.first_name,p.last_name,c.name club_name
        FROM match_events e
        LEFT JOIN players p ON p.id=e.player_id
        JOIN clubs c ON c.id=e.club_id
        WHERE e.match_id=:m AND e.event_type='GOAL'
        ORDER BY e.minute,e.id'''),{'m':match['id']})).mappings().all()
    goal_lines=[]
    for e in goal_events:
        metadata=e.get('metadata') or {}
        if metadata.get('extra_time'):
            goal_minute=str(metadata.get('display_minute') or e['minute'])
        else:
            goal_minute=f"90+{int(e['minute'])-90}" if int(e['minute'])>90 else str(e['minute'])
        goal_lines.append(f"• {goal_minute}′ {display_player_name(e.get('first_name'),e.get('last_name'))} ({e['club_name']})")
    motm_text='—'
    if motm_player_id:
        motm_row=(await s.execute(text('SELECT p.first_name,p.last_name,ps.rating FROM players p JOIN match_player_stats ps ON ps.player_id=p.id AND ps.match_id=:m WHERE p.id=:p'),{'m':match['id'],'p':motm_player_id})).mappings().first()
        if motm_row:
            motm_text=f"{display_player_name(motm_row['first_name'],motm_row['last_name'])} ({float(motm_row['rating'] or 0):.1f})"
    penalty_suffix=f" ({home_pen}:{away_pen} пен.)" if home_pen is not None and away_pen is not None else ''
    result_text=(f"{data.get('home_name','Home')} <b>{final_h}:{final_a}{penalty_suffix}</b> {data.get('away_name','Away')}\n\n"
                  f"⚽ <b>Голы:</b>\n{chr(10).join(goal_lines) if goal_lines else '• Нет голов'}\n\n"
                  f"⭐ <b>MOTM:</b> {motm_text}")
    await queue_club_notification(s,match['home_club_id'],'MATCH','🏁 Матч завершён',result_text,f'match-finished:{match["id"]}')
    await queue_club_notification(s,match['away_club_id'],'MATCH','🏁 Матч завершён',result_text,f'match-finished:{match["id"]}')
    snapshot_ids=(await s.execute(text('SELECT player_id FROM club_players WHERE club_id=ANY(:clubs) ORDER BY club_id,player_id'), {'clubs':[match['home_club_id'],match['away_club_id']]})).scalars().all()
    if snapshot_ids:
        await record_snapshots([int(pid) for pid in snapshot_ids],s,note=f'match:{match["id"]}')
    await apply_match_reputation(s, match['home_club_id'], match['away_club_id'], final_h, final_a)
    from app.game.battle_pass import award_match_points
    await award_match_points(s,match['home_club_id'],match['away_club_id'],final_h,final_a)
    from app.game.inventory import advance_morale_boosts
    await advance_morale_boosts(s,[match['home_club_id'],match['away_club_id']])
    from app.game.daily_quests import record_match
    await record_match(s, int(match['home_club_id']), final_h)
    await record_match(s, int(match['away_club_id']), final_a)
    return {'match_id':match['id'],'home':data.get('home_name','Home'),'away':data.get('away_name','Away'),'home_score':final_h,'away_score':final_a}

async def _advance_live_matches_once(s, league_id=None):
    now=datetime.now(timezone.utc)
    rows=await _live_match_rows(s,league_id)
    transitions=[]; finished=[]
    for match in rows:
        started_at=match.get('started_at')
        if started_at is not None and (now-started_at).total_seconds() > 30*60:
            logger.error('Live match exceeded 30-minute wall-clock safety limit; emergency finalization: match_id=%s minute=%s score=%s:%s', match['id'], match.get('current_minute'), match.get('home_score'), match.get('away_score'))
            result=await _finish_live_match_emergency(s,match)
            if result:
                finished.append(result); transitions.append((match['id'],'FULL_TIME_SAFETY'))
            continue
        if int(match.get('current_minute') or 0) > MAX_MATCH_MINUTES:
            logger.error('Live match exceeded MAX_MATCH_MINUTES; emergency finalization: match_id=%s minute=%s', match['id'], match.get('current_minute'))
            result=await _finish_live_match_emergency(s,match)
            if result:
                finished.append(result); transitions.append((match['id'],'FULL_TIME_SAFETY'))
            continue
        phase=match.get('live_phase') or ('HALFTIME' if match.get('halftime_locked') else 'FIRST_HALF')
        phase_start=match.get('phase_started_at') or match.get('started_at') or now
        elapsed=(now-phase_start).total_seconds()
        elapsed=max(0,elapsed)
        current=int(match.get('current_minute') or 0)

        if phase=='FIRST_HALF':
            wall_target=min(45,int(elapsed//LIVE_SECONDS_PER_MINUTE))
            minute=min(45,current+min(3,max(0,wall_target-current)))
            if minute>current:
                phase_data=match.get('halftime_data') or {}
                await _materialize_live_events(s,match,phase_data,minute)
                hg,ag=await _live_score_at_minute(s,match['id'],minute,match['home_club_id'],match['away_club_id'])
                first_xg=phase_data.get('first_half_xg') or [0,0]
                hp,ap=_live_possession(phase_data,minute)
                await s.execute(text('UPDATE matches SET current_minute=:min,home_score=:hs,away_score=:as_,home_xg=:hx,away_xg=:ax,home_possession=:hp,away_possession=:ap WHERE id=:m'),
                    {'min':minute,'hs':hg,'as_':ag,'hx':clamp_xg(float(first_xg[0])*minute/45),'ax':clamp_xg(float(first_xg[1])*minute/45),'hp':hp,'ap':ap,'m':match['id']})
            if minute>=45:
                first_half_end=phase_start+timedelta(seconds=LIVE_FIRST_HALF_SECONDS)
                end=first_half_end+timedelta(seconds=LIVE_HALFTIME_SECONDS)
                await s.execute(text("UPDATE matches SET current_minute=45,live_phase='HALFTIME',halftime_locked=TRUE,halftime_ends_at=CAST(:end AS timestamptz),phase_started_at=CAST(:phase AS timestamptz) WHERE id=:m"),
                    {'end':end,'phase':first_half_end,'m':match['id']})
                transitions.append((match['id'],'HALFTIME'))

        elif phase=='HALFTIME':
            end=match.get('halftime_ends_at')
            if end is not None and now>=end:
                fresh=(await s.execute(text('SELECT * FROM matches WHERE id=:m FOR UPDATE'),{'m':match['id']})).mappings().first()
                if fresh and fresh['live_phase']=='HALFTIME':
                    second_start=fresh['halftime_ends_at']
                    await _prepare_second_half_live(s,fresh,second_start)
                    transitions.append((match['id'],'SECOND_HALF'))

        elif phase=='SECOND_HALF':
            phase_data=match.get('second_half_data') or {}
            injury_time=max(0,min(5,int(phase_data.get('injury_time') or 0)))
            final_minute=int(phase_data.get('final_minute') or (90+injury_time))
            wall_target=min(final_minute,45+int(elapsed//LIVE_SECONDS_PER_MINUTE))
            minute=min(final_minute,current+min(3,max(0,wall_target-current)))
            if minute>current:
                phase_data=match.get('second_half_data') or {}
                await _materialize_live_events(s,match,phase_data,minute)
                second_h,second_a=await _live_score_at_minute(s,match['id'],minute,match['home_club_id'],match['away_club_id'])
                home_score=int(match['home_halftime_score'] or 0)+second_h
                away_score=int(match['away_halftime_score'] or 0)+second_a
                first_xg=phase_data.get('first_half_xg') or [float(match.get('home_xg') or 0),float(match.get('away_xg') or 0)]
                second_xg=phase_data.get('second_half_xg') or [0,0]
                progress=max(0,min(45,minute-45))/45
                hp,ap=_live_possession(phase_data,minute)
                await s.execute(text('UPDATE matches SET current_minute=:min,home_score=:hs,away_score=:as_,home_xg=:hx,away_xg=:ax,home_possession=:hp,away_possession=:ap WHERE id=:m'),
                    {'min':minute,'hs':home_score,'as_':away_score,'hx':clamp_xg(float(first_xg[0])+float(second_xg[0])*progress),'ax':clamp_xg(float(first_xg[1])+float(second_xg[1])*progress),'hp':hp,'ap':ap,'m':match['id']})
            if minute>=final_minute:
                refreshed=(await s.execute(text('SELECT * FROM matches WHERE id=:m FOR UPDATE'),{'m':match['id']})).mappings().first()
                if refreshed and refreshed['live_phase']=='SECOND_HALF':
                    try:
                        async with s.begin_nested():
                            result=await _finish_live_match(s,refreshed)
                    except Exception:
                        # The begin_nested() context has already rolled back this
                        # match's savepoint. Do not roll back the outer transaction:
                        # it may contain successful work for other live matches.
                        logger.exception('Live finish pipeline failed; using per-match recovery: match_id=%s', match['id'])
                        result=await _force_finish_live_match(s,match['id'])
                    if result:
                        finished.append(result); transitions.append((match['id'],'FULL_TIME'))
    return transitions,finished


async def advance_live_matches(s, league_id=None):
    # Live phase contract: match.get('phase_started_at') or match.get('started_at') or now
    # Second-half xG contract: phase_data.get('first_half_xg')
    # Live materialization contract: _materialize_live_events(s,match,phase_data,minute)
    # Restart timing contract: elapsed=(now-phase_start).total_seconds()
    # Minute cap contract: current+min(3,max(0,wall_target-current))
    return await _with_match_lock_retry(
        lambda: _advance_live_matches_once(s,league_id),
        s,
        'advance_live_matches',
    )


# --- Club management expansion (v2) ---
from app.game.club_management import contract_offer, transfer_quote, training_result, attribute_growth, morale_after_match
from app.game.transfer_interest import calculate_interest
from app.game.player_history import record_snapshot, record_snapshots
from app.game.substitutions import auto_substitution, MAX_SUBSTITUTIONS
from app.game.ai_transfer import ai_transfer_action
from app.game.staff import staff_bonus
from app.game.inbox import send_message as inbox_send_message
from app.game.weather import generate_weather,weather_label
from app.game.reputation import apply_match_reputation, apply_season_reputation
from app.game.market_economy import calculate_market_multiplier
from app.game.infrastructure import ticket_income_multiplier, recovery_multiplier, training_multiplier

async def _transfer_interest_context(s, club_id, player, offered_salary, contract_years=3, release_clause=0):
    club=(await s.execute(text("SELECT id,name,reputation,budget FROM clubs WHERE id=:c"), {'c':club_id})).mappings().first()
    if not club:
        raise ValueError('Клуб не найден.')
    position_count=int((await s.execute(text("SELECT count(*) FROM club_players cp JOIN players p ON p.id=cp.player_id WHERE cp.club_id=:c AND p.position=:pos"), {'c':club_id,'pos':player['position']})).scalar_one())
    current=player.get('current_club')
    target=dict(club)
    target.update({'offered_salary':int(offered_salary),'contract_years':int(contract_years),
                   'release_clause':int(release_clause or 0),'position_count':position_count})
    return calculate_interest(player,target,current)


async def _market_multiplier_for_club(s, club_id):
    rows=(await s.execute(text('''SELECT c.budget,c.sponsor_level,c.reputation
        FROM league_teams lt JOIN clubs c ON c.id=lt.club_id
        WHERE lt.league_id=(SELECT league_id FROM league_teams WHERE club_id=:c LIMIT 1)'''),{'c':club_id})).mappings().all()
    if not rows:
        league={'total_budget':0,'avg_income':0}
    else:
        incomes=[sponsor_income(r['sponsor_level'],float(r['reputation'])) for r in rows]
        league={'total_budget':sum(int(r['budget']) for r in rows),'avg_income':sum(incomes)/len(incomes)}
    multiplier=calculate_market_multiplier(league)
    logger.info('💰 Рынок: множитель цен %.2f', multiplier)
    return multiplier


async def market_players(s, limit=10, listed_only=False):
    where = "tl.status='LISTED'" if listed_only else "(cp.player_id IS NULL OR tl.status='LISTED')"
    # Template rows are a catalogue, not a unique physical player owned by one
    # club. Keep them purchasable after another club has acquired a copy.
    return (await s.execute(text(f'''SELECT p.id,p.first_name,p.last_name,p.position,p.age,p.market_value,p.potential,
        p.pace,p.shooting,p.passing,p.dribbling,p.defending,p.physical,p.stamina,p.mental,p.rarity,
        CASE WHEN p.is_template=TRUE AND tl.id IS NULL THEN NULL ELSE cp.club_id END seller_club_id,
        tl.id listing_id,tl.asking_price,tl.minimum_price
        FROM players p LEFT JOIN club_players cp ON cp.player_id=p.id
        LEFT JOIN transfer_listings tl ON tl.player_id=p.id AND tl.status='LISTED'
        WHERE ({where}) OR (NOT :listed_only AND p.is_template=TRUE)
        ORDER BY COALESCE(tl.asking_price,p.market_value) DESC,p.id LIMIT :n'''),{'n':limit,'listed_only':listed_only})).mappings().all()

async def list_player_for_transfer(s, club_id, player_id, asking_price=None):
    club=(await s.execute(text('SELECT id FROM clubs WHERE id=:c FOR UPDATE'),{'c':club_id})).mappings().first()
    if not club: raise ValueError('Клуб не найден.')
    count=(await s.execute(text('SELECT count(*) FROM club_players WHERE club_id=:c'),{'c':club_id})).scalar_one()
    if count<=18: raise ValueError('В составе должно остаться минимум 18 игроков.')
    row=(await s.execute(text('''SELECT p.*,cp.contract_until,cp.contract_salary,cp.form,cp.morale
        FROM club_players cp JOIN players p ON p.id=cp.player_id
        WHERE cp.club_id=:c AND cp.player_id=:p FOR UPDATE'''),{'c':club_id,'p':player_id})).mappings().first()
    if not row: raise ValueError('Этого игрока нет в вашем составе.')
    existing=(await s.execute(text("SELECT id FROM transfer_listings WHERE player_id=:p AND status='LISTED'"),{'p':player_id})).first()
    if existing: raise ValueError('Игрок уже выставлен на продажу.')
    import datetime
    years=2
    if row['contract_until']:
        years=max(0,(row['contract_until']-datetime.date.today()).days//365)
    q=transfer_quote(row['market_value'],row['age'],row['form'],years,True)
    ask=max(q.minimum_price,int(asking_price) if asking_price else q.asking_price)
    if ask>q.asking_price*2: raise ValueError('Запрашиваемая цена слишком высока для рынка.')
    lid=(await s.execute(text('''INSERT INTO transfer_listings(player_id,seller_club_id,asking_price,minimum_price) VALUES(:p,:c,:a,:m) RETURNING id'''),{'p':player_id,'c':club_id,'a':ask,'m':q.minimum_price})).scalar_one()
    return lid,ask,q.minimum_price

async def cancel_transfer_listing(s, club_id, player_id):
    r=(await s.execute(text("UPDATE transfer_listings SET status='CANCELLED' WHERE player_id=:p AND seller_club_id=:c AND status='LISTED' RETURNING id"),{'p':player_id,'c':club_id})).first()
    if not r: raise ValueError('Active listing not found.')
    return r.id

async def make_transfer_offer(s, buyer_club_id, listing_id, amount):
    amount=int(amount)
    listing=(await s.execute(text("SELECT * FROM transfer_listings WHERE id=:l AND status='LISTED' FOR UPDATE"),{'l':listing_id})).mappings().first()
    if not listing: raise ValueError('Transfer listing is no longer available.')
    if listing['seller_club_id']==buyer_club_id: raise ValueError('You cannot bid for your own player.')
    if amount<listing['minimum_price']: raise ValueError(f'Minimum acceptable price is €{listing["minimum_price"]:,}.')
    club=(await s.execute(text('SELECT budget FROM clubs WHERE id=:c FOR UPDATE'),{'c':buyer_club_id})).mappings().first()
    if not club: raise ValueError('Клуб не найден.')
    if check_bankruptcy(club):
        raise ValueError(BANKRUPTCY_WARNING)
    if club['budget']<amount: raise ValueError(f'Недостаточно бюджета для €{amount:,}.')
    oid=(await s.execute(text('INSERT INTO transfer_offers(listing_id,buyer_club_id,amount) VALUES(:l,:c,:a) RETURNING id'),{'l':listing_id,'c':buyer_club_id,'a':amount})).scalar_one()
    player=(await s.execute(text('SELECT first_name,last_name FROM players WHERE id=:p'),{'p':listing['player_id']})).mappings().first()
    await queue_club_notification(s,listing['seller_club_id'],'TRANSFER','📩 Новое трансферное предложение',f"Предложение €{amount:,} за {display_player_name(player.get('first_name'),player.get('last_name'))}. Решение ожидает тебя.",f'transfer-offer:{oid}')
    return oid

async def accept_transfer_offer(s, seller_club_id, offer_id):
    offer=(await s.execute(text('''SELECT o.*,l.player_id,l.seller_club_id,l.minimum_price,l.status listing_status,
        p.first_name,p.last_name,p.market_value,p.salary,p.potential,p.age,p.position,p.pace,p.shooting,p.passing,p.dribbling,p.defending,p.physical,p.stamina,p.mental,
        cp.contract_salary,cp.release_clause,cp.contract_until
        FROM transfer_offers o JOIN transfer_listings l ON l.id=o.listing_id JOIN players p ON p.id=l.player_id
        LEFT JOIN club_players cp ON cp.player_id=p.id AND cp.club_id=l.seller_club_id
        WHERE o.id=:o AND o.status='PENDING' FOR UPDATE OF o,l,p'''),{'o':offer_id})).mappings().first()
    if not offer or offer['seller_club_id']!=seller_club_id or offer['listing_status']!='LISTED': raise ValueError('You cannot accept this offer.')
    if offer['amount']<offer['minimum_price']: raise ValueError('Offer is below the minimum price.')
    buyer=offer['buyer_club_id']; seller=seller_club_id
    if buyer == seller: raise ValueError('Buyer and seller clubs must differ.')
    locked=(await s.execute(text('SELECT id,budget FROM clubs WHERE id=ANY(:ids) ORDER BY id FOR UPDATE'), {'ids':[buyer,seller]})).mappings().all()
    if len(locked)!=2: raise ValueError('Buyer or seller club not found.')
    count=(await s.execute(text('SELECT count(*) FROM club_players WHERE club_id=:c'),{'c':buyer})).scalar_one()
    if count>=30: raise ValueError('Buyer squad is full.')
    seller_count=(await s.execute(text('SELECT count(*) FROM club_players WHERE club_id=:c'),{'c':seller})).scalar_one()
    if int(seller_count)<=18: raise ValueError('В составе продавца должно остаться минимум 18 игроков.')
    seller_owns_player=(await s.execute(text('SELECT 1 FROM club_players WHERE club_id=:c AND player_id=:p FOR UPDATE'),
        {'c':seller,'p':int(offer['player_id'])})).first()
    if not seller_owns_player: raise ValueError('Игрок больше не принадлежит клубу-продавцу.')
    budget=next(int(r['budget']) for r in locked if r['id']==buyer)
    agreed_amount=int(offer['amount'])
    if budget < 0: raise ValueError(BANKRUPTCY_WARNING)
    if budget<agreed_amount: raise ValueError(f'У покупателя больше недостаточно бюджета для €{agreed_amount:,}.')
    offer = dict(offer)
    offer['amount']=agreed_amount
    buyer_salary=max(15_000,int(offer['amount'])//35)
    offer_data=dict(offer)
    offer_data['current_club']={'contract_salary':offer.get('contract_salary') or offer.get('salary') or 0,'release_clause':offer.get('release_clause') or 0}
    interest=await _transfer_interest_context(s,buyer,offer_data,buyer_salary,contract_years=3,release_clause=max(int(offer['amount'])*2,1_000_000))
    if interest['verdict']=='NOT_INTERESTED':
        reasons='; '.join(interest['reasons']) or 'Игрок не заинтересован в переходе.'
        raise ValueError(f'Игрок не хочет переходить в ваш клуб: {reasons}')
    duplicate=(await s.execute(text('''SELECT 1 FROM club_players cp JOIN players p ON p.id=cp.player_id
        WHERE cp.club_id=:c AND COALESCE(p.template_id,p.id)=COALESCE((SELECT p2.template_id FROM players p2 WHERE p2.id=:p),:p) LIMIT 1'''),
        {'c':buyer,'p':offer['player_id']})).first()
    if duplicate: raise ValueError('У вас уже есть такой игрок.')
    await record_financial_transaction(s,buyer,'TRANSFER_OUT',-offer['amount'],f'Трансфер: {display_player_name(offer.get("first_name"),offer.get("last_name"))}')
    await record_financial_transaction(s,seller,'TRANSFER_IN',offer['amount'],f'Продажа: {display_player_name(offer.get("first_name"),offer.get("last_name"))}')
    await s.execute(text('DELETE FROM club_players WHERE club_id=:c AND player_id=:p'),{'c':seller,'p':offer['player_id']})
    await s.execute(text("""INSERT INTO club_players(club_id,player_id,template_id,contract_salary,release_clause,contract_until) SELECT :c,:p,COALESCE(template_id,id),:sal,:clause,CURRENT_DATE+INTERVAL '3 years' FROM players WHERE id=:p"""), {'c':club_id,'p':acquired_player_id,'sal':max(15000,price//35),'clause':max(price*2,1000000)})
    await s.execute(text("UPDATE transfer_listings SET status='SOLD' WHERE id=:l"),{'l':offer['listing_id']})
    await s.execute(text("UPDATE transfer_offers SET status=CASE WHEN id=:o THEN 'ACCEPTED' ELSE 'REJECTED' END WHERE listing_id=:l AND status='PENDING'"),{'o':offer_id,'l':offer['listing_id']})
    await s.execute(text('INSERT INTO transfers(player_id,from_club_id,to_club_id,price) VALUES(:p,:f,:t,:a)'),{'p':offer['player_id'],'f':seller,'t':buyer,'a':offer['amount']})
    pname=display_player_name(offer.get('first_name'),offer.get('last_name'))
    await queue_club_notification(s,buyer,'TRANSFER','🤝 Трансфер завершён',f'{pname} перешёл в твой клуб за €{offer["amount"]:,}.',f'transfer-complete:{offer["id"]}:buyer')
    await queue_club_notification(s,seller,'TRANSFER','💰 Игрок продан',f'{pname} продан за €{offer["amount"]:,}.',f'transfer-complete:{offer["id"]}:seller')
    await inbox_send_message(buyer,'🤝 Трансфер завершён',f'{pname} перешёл в твой клуб за €{offer["amount"]:,}.',s)
    await inbox_send_message(seller,'💰 Игрок продан',f'{pname} продан за €{offer["amount"]:,}.',s)
    return offer

async def buy_player(s, club_id, player_id):
    player=(await s.execute(text('''SELECT p.id,p.first_name,p.last_name,p.market_value,p.salary,p.age,p.potential,p.position,
        p.pace,p.shooting,p.passing,p.dribbling,p.defending,p.physical,p.stamina,p.mental,
        p.is_template,p.template_id,COALESCE(p.template_id,p.id) AS source_template_id
        FROM players p
        WHERE p.id=:p AND (p.is_template=TRUE OR NOT EXISTS(
            SELECT 1 FROM club_players cp WHERE cp.player_id=p.id)) FOR UPDATE'''),{'p':player_id})).mappings().first()
    if not player: raise ValueError('Игрок больше недоступен на рынке.')
    club=(await s.execute(text('SELECT budget FROM clubs WHERE id=:c FOR UPDATE'),{'c':club_id})).mappings().first()
    if not club: raise ValueError('Клуб не найден.')
    if check_bankruptcy(club):
        raise ValueError(BANKRUPTCY_WARNING)
    squad_size=int((await s.execute(text('SELECT count(*) FROM club_players WHERE club_id=:c'),{'c':club_id})).scalar_one())
    if squad_size>=30: raise ValueError('Состав клуба заполнен: максимум 30 игроков.')
    duplicate=(await s.execute(text('''SELECT 1 FROM club_players cp JOIN players p ON p.id=cp.player_id
        WHERE cp.club_id=:c AND COALESCE(cp.template_id,p.template_id,p.id)=:t LIMIT 1'''),
        {'c':club_id,'t':int(player['source_template_id'])})).first()
    if duplicate: raise ValueError('У вас уже есть такой игрок.')
    market_multiplier=await _market_multiplier_for_club(s,club_id)
    price=int(round(int(player['market_value'])*market_multiplier))
    if int(club['budget'])<price: raise ValueError(f'Недостаточно бюджета. Нужно €{price:,}.')
    interest=await _transfer_interest_context(s,club_id,player,max(15_000,price//35),contract_years=3,release_clause=max(price*2,1_000_000))
    if interest['verdict']=='NOT_INTERESTED':
        reasons='; '.join(interest['reasons']) or 'Игрок не заинтересован в переходе.'
        raise ValueError(f'Игрок не хочет переходить в ваш клуб: {reasons}')

    if bool(player.get('is_template')):
        # Never attach the catalogue template row directly to a club. Each club
        # gets its own player instance while the source template remains reusable.
        acquired_player_id,_=await add_copy_to_club(s,club_id,int(player['id']))
        if acquired_player_id is None:
            raise ValueError('У вас уже есть такой игрок.')
        await s.execute(text('UPDATE club_players SET contract_salary=:sal,release_clause=:clause WHERE club_id=:c AND player_id=:p'),
            {'sal':max(15000,price//35),'clause':max(price*2,1000000),'c':club_id,'p':acquired_player_id})
    else:
        acquired_player_id=int(player['id'])
        await s.execute(text('INSERT INTO club_players(club_id,player_id,template_id,contract_salary,release_clause) SELECT :c,:p,COALESCE(template_id,id),:sal,:clause FROM players WHERE id=:p'),{'c':club_id,'p':acquired_player_id,'sal':max(15000,price//35),'clause':max(price*2,1000000)})

    await record_financial_transaction(s,club_id,'TRANSFER_OUT',-price,f'Покупка {display_player_name(player.get("first_name"),player.get("last_name"))} за €{price:,}')
    await s.execute(text('INSERT INTO transfers(player_id,from_club_id,to_club_id,price) VALUES(:p,NULL,:c,:price)'),{'p':acquired_player_id,'c':club_id,'price':price})
    pname=display_player_name(player.get('first_name'),player.get('last_name'))
    await inbox_send_message(club_id,'🤝 Покупка завершена',f'{pname} перешёл в твой клуб за €{price:,}.',s)
    result=dict(player)
    result['id']=acquired_player_id
    return result,price

async def sell_player(s, club_id, player_id):
    club=(await s.execute(text('SELECT id FROM clubs WHERE id=:c FOR UPDATE'),{'c':club_id})).mappings().first()
    if not club: raise ValueError('Клуб не найден.')
    count=(await s.execute(text('SELECT count(*) FROM club_players WHERE club_id=:c'),{'c':club_id})).scalar_one()
    if count<=18: raise ValueError('В составе должно остаться минимум 18 игроков.')
    row=(await s.execute(text('''SELECT p.id,p.first_name,p.last_name,p.market_value,cp.shirt_number FROM club_players cp JOIN players p ON p.id=cp.player_id WHERE cp.club_id=:c AND cp.player_id=:p FOR UPDATE'''),{'c':club_id,'p':player_id})).mappings().first()
    if not row: raise ValueError('Этого игрока нет в вашем составе.')
    price=max(50_000,int(row['market_value']*0.70))
    await s.execute(text("UPDATE transfer_listings SET status='CANCELLED' WHERE player_id=:p AND seller_club_id=:c AND status='LISTED'"),{'p':player_id,'c':club_id})
    await s.execute(text('DELETE FROM club_players WHERE club_id=:c AND player_id=:p'),{'c':club_id,'p':player_id})
    await record_financial_transaction(s,club_id,'TRANSFER_IN',price,f'Продажа {display_player_name(row.get("first_name"),row.get("last_name"))} за €{price:,}')
    await s.execute(text('INSERT INTO transfers(player_id,from_club_id,to_club_id,price) VALUES(:p,:c,NULL,:price)'),{'p':player_id,'c':club_id,'price':price})
    pname=display_player_name(row.get('first_name'),row.get('last_name'))
    await inbox_send_message(club_id,'💰 Продажа завершена',f'{pname} продан за €{price:,}.',s)
    return row,price

async def renew_contract(s, club_id, player_id, years=3, role=None):
    row=(await s.execute(text('''SELECT p.*,cp.contract_salary,cp.morale,cp.playing_time_expectation FROM club_players cp JOIN players p ON p.id=cp.player_id WHERE cp.club_id=:c AND cp.player_id=:p FOR UPDATE'''),{'c':club_id,'p':player_id})).mappings().first()
    if not row: raise ValueError('Игрок не входит в ваш состав.')
    role=role or row['playing_time_expectation']
    overall=sum(row[a] for a in ('pace','shooting','passing','dribbling','defending','physical','stamina','mental'))//8
    offer=contract_offer(row['market_value'],overall,row['age'],years,role)
    import datetime
    await s.execute(text('UPDATE club_players SET contract_until=:d,contract_salary=:sal,release_clause=:clause,playing_time_expectation=:role WHERE club_id=:c AND player_id=:p'),{'d':datetime.date.today()+datetime.timedelta(days=365*offer.years),'sal':offer.salary,'clause':offer.release_clause,'role':role,'c':club_id,'p':player_id})
    return offer

async def set_training_plan(s, club_id, focus, intensity):
    focus=str(focus).upper(); intensity=str(intensity).upper()
    if focus not in {'ATTACK','PLAYMAKING','DEFENSE','PHYSICAL','TECHNICAL','MENTAL','BALANCED','RECOVERY'}: raise ValueError('Неизвестная специализация тренировки.')
    if intensity not in {'LIGHT','NORMAL','HIGH'}: raise ValueError('Неизвестная интенсивность тренировки.')
    await s.execute(text('''INSERT INTO club_training(club_id,focus,intensity) VALUES(:c,:f,:i)
        ON CONFLICT(club_id) DO UPDATE SET focus=EXCLUDED.focus,intensity=EXCLUDED.intensity,updated_at=now()'''),{'c':club_id,'f':focus,'i':intensity})
    await s.execute(text('UPDATE club_players SET training_focus=:f,training_intensity=:i WHERE club_id=:c'),{'c':club_id,'f':focus,'i':intensity})
    return focus,intensity

async def train_squad(s, club_id):
    # Serialize training attempts per club before checking the cooldown. Without
    # this lock, two simultaneous requests can both pass the cooldown and award
    # development twice before either transaction commits.
    club_lock=(await s.execute(text('SELECT id FROM clubs WHERE id=:c FOR UPDATE'),{'c':club_id})).first()
    if not club_lock:
        raise ValueError('Клуб не найден.')
    plan=(await s.execute(text("SELECT focus,intensity FROM club_training WHERE club_id=:c"),{'c':club_id})).mappings().first() or {'focus':'BALANCED','intensity':'NORMAL'}
    last_training_at=(await s.execute(text("SELECT max(last_training_at) FROM club_players WHERE club_id=:c"),{'c':club_id})).scalar_one()
    if last_training_at is not None:
        recent=(await s.execute(text("SELECT now() - CAST(:t AS timestamptz) < interval '20 hours'"), {'t': last_training_at})).scalar_one()
        if recent:
            remaining=(await s.execute(text("SELECT GREATEST(0, EXTRACT(EPOCH FROM (interval '20 hours' - (now() - CAST(:t AS timestamptz)))))"), {'t': last_training_at})).scalar_one()
            return [('COOLDOWN', int(remaining or 0), False)], None
    club_staff=(await s.execute(text('SELECT training_level,staff_training FROM clubs WHERE id=:c'),{'c':club_id})).mappings().first() or {}
    training_level=int(club_staff.get('training_level') or 1)
    staff_training=int(club_staff.get('staff_training') or 1)
    rows=(await s.execute(text('''SELECT cp.*,p.* FROM club_players cp JOIN players p ON p.id=cp.player_id WHERE cp.club_id=:c FOR UPDATE'''),{'c':club_id})).mappings().all()
    changes=[]
    # One batch id groups every per-player development row produced by this run.
    batch_id=token_urlsafe(18)
    for r in rows:
        overall=sum(r[a] for a in ('pace','shooting','passing','dribbling','defending','physical','stamina','mental'))/8
        tr=training_result(r['age'],r['potential'],overall,plan['focus'],plan['intensity'],r['minutes_played'],r['morale'],r['appearances'])
        dev_points=tr.development_points*training_multiplier(training_level)*staff_bonus('training',staff_training)
        updates={}
        for attr in ('pace','shooting','passing','dribbling','defending','physical','stamina','mental'):
            nv=attribute_growth(r[attr],r['potential'],dev_points,plan['focus'],attr)
            if nv!=r[attr]: updates[attr]=nv
        if updates:
            await s.execute(text('UPDATE players SET '+','.join(f'{a}=:{a}' for a in updates)+' WHERE id=:p'),{**updates,'p':r['player_id']})
        await s.execute(text('UPDATE club_players SET morale=GREATEST(0,LEAST(100,morale+:m)),development_points=development_points+:pts,last_training_at=now() WHERE club_id=:c AND player_id=:p'),{'m':tr.morale_delta,'pts':dev_points,'c':club_id,'p':r['player_id']})
        await s.execute(text('INSERT INTO player_development_log(club_id,player_id,focus,intensity,points,changes,batch_id) VALUES(:c,:p,:f,:i,:pts,:changes,:batch)'),{'c':club_id,'p':r['player_id'],'f':plan['focus'],'i':plan['intensity'],'pts':dev_points,'changes':json.dumps(updates),'batch':batch_id})
        changes.append((r['player_id'],dev_points,updates))
    return changes,batch_id

async def training_history(s, club_id, limit=10):
    return (await s.execute(text('''SELECT batch_id,focus,MAX(intensity) intensity,MAX(created_at) created_at,COUNT(*) player_count,
        COUNT(*) FILTER (WHERE changes <> '{}'::jsonb) progressed,COALESCE(SUM(points),0) points
        FROM player_development_log
        WHERE club_id=:c AND batch_id IS NOT NULL
        GROUP BY batch_id,focus
        ORDER BY MAX(created_at) DESC,batch_id DESC LIMIT :n'''),
        {'c':club_id,'n':limit})).mappings().all()

async def training_details(s, club_id, batch_id):
    return (await s.execute(text('''SELECT l.player_id,p.first_name,p.last_name,p.position,l.focus,l.intensity,l.points,l.changes,l.created_at
        FROM player_development_log l JOIN players p ON p.id=l.player_id
        WHERE l.club_id=:c AND l.batch_id=:b AND l.changes <> '{}'::jsonb
        ORDER BY l.points DESC,l.created_at,l.player_id'''),
        {'c':club_id,'b':batch_id})).mappings().all()

async def set_player_expectation(s, club_id, player_id, expectation):
    expectation=str(expectation).upper()
    if expectation not in {'STAR','KEY','SQUAD','PROSPECT'}: raise ValueError('Неизвестное ожидание игрового времени.')
    r=(await s.execute(text('UPDATE club_players SET playing_time_expectation=:e WHERE club_id=:c AND player_id=:p RETURNING player_id'),{'e':expectation,'c':club_id,'p':player_id})).first()
    if not r: raise ValueError('Игрок не входит в ваш состав.')
    return expectation

async def player_management_report(s, club_id, limit=30):
    return (await s.execute(text('''SELECT p.id,p.first_name,p.last_name,p.position,p.age,p.market_value,p.potential,p.pace,p.shooting,p.passing,p.dribbling,p.defending,p.physical,p.stamina,p.mental,
        cp.contract_until,cp.contract_salary,cp.release_clause,cp.morale,cp.playing_time_expectation,cp.appearances,cp.starts,cp.minutes_played,cp.fitness,cp.form,cp.is_injured
        FROM club_players cp JOIN players p ON p.id=cp.player_id WHERE cp.club_id=:c ORDER BY p.position,p.potential DESC,p.id LIMIT :n'''),{'c':club_id,'n':limit})).mappings().all()

async def _persist_fast_match_result(s, match, home, away, ht, at, hb, ab, hc, ac, result):
    """Persist a complete deterministic match without entering the live clock."""
    await _save_lineup_and_tactics(s,match['id'],match['home_club_id'],home,ht)
    await _save_lineup_and_tactics(s,match['id'],match['away_club_id'],away,at)
    event_rows=[]
    sorted_events=sorted(result.events,key=lambda e:(e.minute,e.type,e.player or ''))
    substitution_ids=set()
    for ev in sorted_events:
        if ev.type=='SUBSTITUTION' and ev.player_id is not None:
            m=re.search(r'#(\d+)',sanitize_event_text(ev.text or ''))
            substitution_ids.add(int(ev.player_id))
            if m: substitution_ids.add(int(m.group(1)))
    by_id={int(r['id']):r for r in (await s.execute(text('SELECT id,first_name,last_name FROM players WHERE id=ANY(:ids)'),{'ids':list(substitution_ids)})).mappings().all()} if substitution_ids else {}
    for ev in sorted_events:
        cid=match['home_club_id'] if ev.team in {'HOME',match['home_name']} else match['away_club_id']
        pid=int(ev.player_id) if ev.player_id is not None else None; sid=int(ev.secondary_player_id) if ev.secondary_player_id is not None else None
        description=sanitize_event_text(ev.text or '')
        if ev.type=='SUBSTITUTION' and pid is not None:
            mo=re.search(r'#(\d+)',description)
            if mo and pid in by_id and int(mo.group(1)) in by_id:
                oid=int(mo.group(1)); description=f'⬆️ Вышел: {display_player_name(by_id[pid]["first_name"],by_id[pid]["last_name"])}, ⬇️ Ушёл: {display_player_name(by_id[oid]["first_name"],by_id[oid]["last_name"])}'
        event_rows.append((int(ev.minute),ev.type,cid,pid,sid,description,ev.metadata or {}))
    if event_rows:
        await s.execute(text('''INSERT INTO match_events(match_id,minute,event_type,club_id,player_id,secondary_player_id,description,metadata,is_added_time)
            VALUES(:m,:min,:t,:c,:p,:sp,:d,CAST(:meta AS jsonb),:added)'''),[{'m':match['id'],'min':mi,'t':typ,'c':cid,'p':pid,'sp':sid,'d':desc,'meta':json.dumps(meta or {}),'added':bool((meta or {}).get('extra_time')) or int(mi)>90} for mi,typ,cid,pid,sid,desc,meta in event_rows])
    await _ensure_foul_events(s,match['id'],match['home_club_id'],match['away_club_id'],[p.id for p in home],[p.id for p in away])
    home_shots=sum(1 for _,typ,cid,_,_,_,meta in event_rows if typ=='SHOT' and cid==match['home_club_id'])
    away_shots=sum(1 for _,typ,cid,_,_,_,meta in event_rows if typ=='SHOT' and cid==match['away_club_id'])
    home_shots_on_target=sum(1 for _,typ,cid,_,_,_,meta in event_rows if typ=='SHOT' and cid==match['home_club_id'] and str((meta or {}).get('result','')).upper() in {'GOAL','SAVED'})
    away_shots_on_target=sum(1 for _,typ,cid,_,_,_,meta in event_rows if typ=='SHOT' and cid==match['away_club_id'] and str((meta or {}).get('result','')).upper() in {'GOAL','SAVED'})
    home_corners=sum(1 for _,typ,cid,_,_,_,_ in event_rows if typ=='CORNER' and cid==match['home_club_id'])
    away_corners=sum(1 for _,typ,cid,_,_,_,_ in event_rows if typ=='CORNER' and cid==match['away_club_id'])
    goals_by_player=defaultdict(int); assists_by_player=defaultdict(int)
    for _,typ,_,pid,_,_,_ in event_rows:
        if typ=='GOAL' and pid: goals_by_player[pid]+=1
        if typ=='ASSIST' and pid: assists_by_player[pid]+=1
    motm_id=next((p.id for p in home+away if p.name == result.man_of_the_match), None) if result.man_of_the_match else None
    stat_rows=[]
    for p in home+away:
        rating=float(result.ratings.get(p.name,6.2))
        stat_rows.append({'m':match['id'],'c':match['home_club_id'] if p in home else match['away_club_id'],'p':p.id,'r':rating,'g':goals_by_player.get(p.id,0),'a':assists_by_player.get(p.id,0)})
    if stat_rows:
        await s.execute(text('''INSERT INTO match_player_stats(match_id,club_id,player_id,rating,minutes,goals,assists)
            VALUES(:m,:c,:p,:r,:mins,:g,:a)
            ON CONFLICT(match_id,player_id) DO UPDATE SET rating=EXCLUDED.rating,minutes=EXCLUDED.minutes,goals=EXCLUDED.goals,assists=EXCLUDED.assists'''),[{**row,'mins':int(getattr(result,'final_minute',90))} for row in stat_rows])
    await s.execute(text("""UPDATE matches SET home_score=:h,away_score=:a,home_xg=:hx,away_xg=:ax,
        home_possession=50,away_possession=50,home_shots=:hsht,away_shots=:asht,home_shots_on_target=:hsot,away_shots_on_target=:asot,home_corners=:hc,away_corners=:ac,current_minute=:final_minute,status='FINISHED',live_phase='FINISHED',
        halftime_locked=FALSE,started_at=COALESCE(started_at,now()),finished_at=now(),motm_player_id=:motm,
        home_halftime_score=NULL,away_halftime_score=NULL,halftime_data='{}'::jsonb,second_half_data='{}'::jsonb
        WHERE id=:m"""),
        {'h':int(result.home_score),'a':int(result.away_score),'hx':clamp_xg(result.home_xg),'ax':clamp_xg(result.away_xg),'hsht':home_shots,'asht':away_shots,'hsot':home_shots_on_target,'asot':away_shots_on_target,'hc':home_corners,'ac':away_corners,'final_minute':int(getattr(result,'final_minute',90)),'motm':motm_id,'m':match['id']})
    await s.execute(text("""UPDATE notifications n
        SET cancelled_at=COALESCE(n.cancelled_at,now()),processing_at=NULL
        WHERE n.sent_at IS NULL AND n.failed_at IS NULL AND n.cancelled_at IS NULL
          AND n.dedupe_key LIKE 'match-start-10:%'
          AND split_part(n.dedupe_key,':',5)::bigint=:m"""), {'m':match['id']})
    snapshot_ids=(await s.execute(text('SELECT player_id FROM club_players WHERE club_id=ANY(:clubs) ORDER BY club_id,player_id'), {'clubs':[match['home_club_id'],match['away_club_id']]})).scalars().all()
    if snapshot_ids:
        await record_snapshots([int(pid) for pid in snapshot_ids],s,note=f'match:{match["id"]}')
    await apply_match_reputation(s, match['home_club_id'], match['away_club_id'], int(result.home_score), int(result.away_score))
    from app.game.battle_pass import award_match_points
    await award_match_points(s,match['home_club_id'],match['away_club_id'],int(result.home_score),int(result.away_score))
    from app.game.inventory import advance_morale_boosts
    await advance_morale_boosts(s,[match['home_club_id'],match['away_club_id']])
    from app.game.daily_quests import record_match
    await record_match(s, int(match['home_club_id']), int(result.home_score))
    await record_match(s, int(match['away_club_id']), int(result.away_score))
    return {'match_id':match['id'],'home':match['home_name'],'away':match['away_name'],
            'home_score':int(result.home_score),'away_score':int(result.away_score)}


async def create_user_friendly_match(s, home_club_id, mode="friendly"):
    """Start a paced, no-stakes live friendly/test match for the user's club."""
    from random import SystemRandom, Random
    from app.game.engine import simulate_first_half

    home = (await s.execute(text(
        "SELECT id,name FROM clubs WHERE id=:c AND is_test=FALSE FOR UPDATE"
    ), {"c": int(home_club_id)})).mappings().first()
    if home is None:
        raise ValueError("Не удалось найти основной клуб пользователя.")

    # Idempotency: repeated taps reuse the active match instead of creating duplicates.
    recent = (await s.execute(text("""SELECT m.id,m.home_club_id,m.away_club_id,
            hc.name AS home_name,ac.name AS away_name,m.home_score,m.away_score,
            COALESCE(m.current_minute,0) AS minute,m.status
        FROM matches m JOIN clubs hc ON hc.id=m.home_club_id JOIN clubs ac ON ac.id=m.away_club_id
        WHERE m.home_club_id=:c AND m.is_test=TRUE AND m.competition_type='FRIENDLY'
          AND (m.status='LIVE' OR (m.status='FINISHED' AND m.finished_at >= now()-INTERVAL '10 seconds'))
        ORDER BY CASE WHEN m.status='LIVE' THEN 0 ELSE 1 END,m.id DESC LIMIT 1"""),
        {"c": int(home_club_id)})).mappings().first()
    if recent:
        return {"status":str(recent["status"]).lower(), "mode":mode, "match_id":int(recent["id"]),
            "home_club_id":int(recent["home_club_id"]), "away_club_id":int(recent["away_club_id"]),
            "home_name":str(recent["home_name"]), "away_name":str(recent["away_name"]),
            "home_score":int(recent["home_score"] or 0), "away_score":int(recent["away_score"] or 0),
            "minute":int(recent["minute"] or 0),
            "competition_name":"Тестовый матч" if mode == "test" else "Товарищеский матч",
            "reused":True}

    away = (await s.execute(text("""SELECT c.id,c.name FROM clubs c
        WHERE c.is_test=TRUE AND c.id<>:home
          AND NOT EXISTS (SELECT 1 FROM league_teams lt WHERE lt.club_id=c.id)
        ORDER BY c.id LIMIT 1 FOR UPDATE OF c"""), {"home": int(home_club_id)})).mappings().first()
    if away is None:
        away_id, _ = await create_test_bot_club(s, is_test=True)
        away = (await s.execute(text("SELECT id,name FROM clubs WHERE id=:c FOR UPDATE"),
                                {"c": int(away_id)})).mappings().one()

    home_id, away_id = int(home["id"]), int(away["id"])
    home_players = await _starting_players(s, home_id)
    away_players = await _starting_players(s, away_id)
    if len(home_players) != 11 or len(away_players) != 11:
        raise ValueError("Для запуска матча обеим командам нужны 11 доступных игроков.")
    await train_squad(s, home_id)
    await train_squad(s, away_id)
    home_tactics, away_tactics = await _club_tactics(s, home_id), await _club_tactics(s, away_id)
    home_board = await _club_board(s, home_id, home_players, home_tactics.formation)
    away_board = await _club_board(s, away_id, away_players, away_tactics.formation)
    home_counter, away_counter = await get_counterplan(s, home_id, away_id, is_friendly=True), await get_counterplan(s, away_id, home_id, is_friendly=True)
    seed = SystemRandom().randint(1, 2_000_000_000)
    weather = generate_weather(Random(seed * 1009 + 1))
    hs, as_, events = simulate_first_half(home_players, away_players, home_tactics, away_tactics,
        seed * 2 + 1, home_counter, away_counter, home_board, away_board, weather)
    full_hx, full_ax = expected_goals(home_players, away_players, home_tactics, away_tactics,
        home_board, away_board, home_counter, away_counter, {"weather":weather})
    full_hx += sum(float((e.metadata or {}).get("xg_bonus",0.0)) for e in events
                   if e.type=="MOMENT_OF_BRILLIANCE" and e.team=="HOME")
    full_ax += sum(float((e.metadata or {}).get("xg_bonus",0.0)) for e in events
                   if e.type=="MOMENT_OF_BRILLIANCE" and e.team=="AWAY")
    phase_start = datetime.now(timezone.utc)
    halftime_data = {
        "home_tactics":_tactics_json(home_tactics), "away_tactics":_tactics_json(away_tactics),
        "events":[e.__dict__ for e in events],
        "home_board":[(z.x,z.y,z.role,z.instruction) for z in home_board.slots],
        "away_board":[(z.x,z.y,z.role,z.instruction) for z in away_board.slots],
        "home_players":[p.id for p in home_players], "away_players":[p.id for p in away_players],
        "first_half_xg":[round(full_hx*.48,2),round(full_ax*.48,2)],
        "full_xg":[clamp_xg(full_hx),clamp_xg(full_ax)],
        "weather":weather,
    }
    match_id = int((await s.execute(text("""INSERT INTO matches(
        league_id,season_id,round,home_club_id,away_club_id,seed,status,competition_type,
        scheduled_at,started_at,current_minute,is_test,live_phase,home_score,away_score,
        home_halftime_score,away_halftime_score,home_xg,away_xg,home_possession,away_possession,
        halftime_data,second_half_data,phase_started_at,live_event_cursor,halftime_locked)
        VALUES(NULL,NULL,0,:home,:away,:seed,'LIVE','FRIENDLY',:now,:now,0,TRUE,'FIRST_HALF',
        0,0,:hs,:as_,0,0,50,50,CAST(:data AS jsonb),'{}'::jsonb,:now,0,FALSE)
        RETURNING id"""), {"home":home_id,"away":away_id,"seed":seed,"now":phase_start,
            "hs":int(hs),"as_":int(as_),"data":json.dumps(halftime_data,ensure_ascii=False,default=str)})).scalar_one())
    await _save_lineup_and_tactics(s, match_id, home_id, home_players, home_tactics)
    await _save_lineup_and_tactics(s, match_id, away_id, away_players, away_tactics)
    for cid in (home_id, away_id):
        await queue_club_notification(s,cid,'MATCH','▶️ Матч начался',
            'Стартовый свисток! Откройте live-просмотр матча.',f'match-started:{match_id}')
    return {"status":"live","mode":mode,"match_id":match_id,
        "home_club_id":home_id,"away_club_id":away_id,
        "home_name":str(home["name"]),"away_name":str(away["name"]),
        "home_score":0,"away_score":0,"minute":0,
        "competition_name":"Тестовый матч" if mode=="test" else "Товарищеский матч",
        "reused":False}


async def create_admin_test_match(s, admin_telegram_id):
    """Create and instantly simulate one isolated tagged friendly match.

    The test match has no league/season, does not alter standings, and awards no
    normal match economy, Battle Pass points, or daily-quest progress.
    """
    from random import SystemRandom
    admin_telegram_id = int(admin_telegram_id)
    owner_id = (await s.execute(text("SELECT id FROM users WHERE telegram_id=:tg FOR UPDATE"),
                                {'tg': admin_telegram_id})).scalar_one_or_none()
    if owner_id is None:
        owner_id = (await s.execute(text("""INSERT INTO users(telegram_id,first_name)
            VALUES(:tg,'Admin') ON CONFLICT(telegram_id) DO UPDATE SET telegram_id=EXCLUDED.telegram_id
            RETURNING id"""), {'tg': admin_telegram_id})).scalar_one()

    home = (await s.execute(text("""SELECT id,name FROM clubs
        WHERE owner_user_id=:u AND is_test=FALSE ORDER BY id LIMIT 1 FOR UPDATE"""),
        {'u': int(owner_id)})).mappings().first()
    if home is None:
        # Reuse an existing admin-owned temporary test club before creating another one.
        home = (await s.execute(text("""SELECT id,name FROM clubs
            WHERE owner_user_id=:u AND is_test=TRUE ORDER BY id LIMIT 1 FOR UPDATE"""),
            {'u': int(owner_id)})).mappings().first()
    if home is None:
        # Create a temporary admin-owned test club only if no club exists at all.
        temporary_id, _ = await create_test_bot_club(s, admin_id=admin_telegram_id, is_test=True)
        await s.execute(text("UPDATE clubs SET owner_user_id=:u WHERE id=:c"),
                        {'u': int(owner_id), 'c': int(temporary_id)})
        home = (await s.execute(text("SELECT id,name FROM clubs WHERE id=:c FOR UPDATE"),
                                {'c': int(temporary_id)})).mappings().one()

    away_id, _ = await create_test_bot_club(s, admin_id=admin_telegram_id, is_test=True)
    away = (await s.execute(text("SELECT id,name FROM clubs WHERE id=:c FOR UPDATE"),
                            {'c': int(away_id)})).mappings().one()
    seed = SystemRandom().randint(1, 2_000_000_000)
    match_id = int((await s.execute(text("""INSERT INTO matches(
            league_id,season_id,round,home_club_id,away_club_id,seed,status,competition_type,
            scheduled_at,started_at,current_minute,is_test,live_phase)
        VALUES(NULL,NULL,0,:home,:away,:seed,'SCHEDULED','FRIENDLY',now(),now(),0,TRUE,'SCHEDULED')
        RETURNING id"""), {'home': int(home['id']), 'away': int(away['id']), 'seed': seed})).scalar_one())

    home_players = await _starting_players(s, int(home['id']))
    away_players = await _starting_players(s, int(away['id']))
    home_tactics = await _club_tactics(s, int(home['id']))
    away_tactics = await _club_tactics(s, int(away['id']))
    home_board = await _club_board(s, int(home['id']), home_players, home_tactics.formation)
    away_board = await _club_board(s, int(away['id']), away_players, away_tactics.formation)
    home_counter = await get_counterplan(s, int(home['id']), int(away['id']), is_friendly=True)
    away_counter = await get_counterplan(s, int(away['id']), int(home['id']), is_friendly=True)
    result = simulate_match(str(home['name']), str(away['name']), home_players, away_players,
                            home_tactics, away_tactics, seed, home_board, away_board,
                            home_counter, away_counter)
    await _save_lineup_and_tactics(s, match_id, int(home['id']), home_players, home_tactics)
    await _save_lineup_and_tactics(s, match_id, int(away['id']), away_players, away_tactics)

    event_rows=[]
    for event in sorted(result.events, key=lambda e:(int(e.minute), str(e.type))):
        event_club = int(home['id']) if event.team in {'HOME', str(home['name'])} else int(away['id'])
        event_rows.append({
            'm': match_id, 'minute': int(event.minute), 'kind': str(event.type),
            'club': event_club,
            'player': int(event.player_id) if event.player_id is not None else None,
            'secondary': int(event.secondary_player_id) if event.secondary_player_id is not None else None,
            'description': sanitize_event_text(event.text or str(event.type)),
            'metadata': json.dumps(event.metadata or {}, ensure_ascii=False, default=str),
            'added': bool((event.metadata or {}).get('extra_time')) or int(event.minute) > 90,
        })
    if event_rows:
        await s.execute(text("""INSERT INTO match_events(
                match_id,minute,event_type,club_id,player_id,secondary_player_id,description,metadata,is_added_time)
            VALUES(:m,:minute,:kind,:club,:player,:secondary,:description,CAST(:metadata AS jsonb),:added)"""), event_rows)

    final_minute = int(getattr(result, 'final_minute', 90) or 90)
    await s.execute(text("""UPDATE matches SET home_score=:hs,away_score=:as,
        current_minute=:minute,status='FINISHED',live_phase='FINISHED',started_at=COALESCE(started_at,now()),
        finished_at=now(),home_xg=:hx,away_xg=:ax,halftime_locked=FALSE,is_test=TRUE
        WHERE id=:m"""), {
        'hs': int(result.home_score), 'as': int(result.away_score), 'minute': final_minute,
        'hx': clamp_xg(float(getattr(result, 'home_xg', 0) or 0)),
        'ax': clamp_xg(float(getattr(result, 'away_xg', 0) or 0)), 'm': match_id,
    })
    logger.info('Admin test friendly completed: match=%s home=%s away=%s score=%s:%s',
                match_id, int(home['id']), int(away['id']), int(result.home_score), int(result.away_score))
    return {
        'match_id': match_id, 'home_club_id': int(home['id']), 'away_club_id': int(away['id']),
        'home_name': str(home['name']), 'away_name': str(away['name']),
        'home_score': int(result.home_score), 'away_score': int(result.away_score),
        'minute': final_minute,
    }


async def create_admin_live_test_match(s, admin_telegram_id):
    """Create/reuse a paced LIVE test match for the admin's real club."""
    admin_telegram_id = int(admin_telegram_id)
    club = (await s.execute(text("""SELECT c.id FROM clubs c
        JOIN users u ON u.id=c.owner_user_id
        WHERE u.telegram_id=:tg AND COALESCE(c.is_test,FALSE)=FALSE
        ORDER BY c.id LIMIT 1 FOR UPDATE OF c"""), {'tg': admin_telegram_id})).mappings().first()
    if not club:
        raise ValueError('Для LIVE-теста нужен основной клуб администратора.')
    return await create_user_friendly_match(s, int(club['id']), mode='test')


async def delete_admin_test_match(s, match_id):
    """Delete only a tagged friendly test match and orphaned tagged test clubs."""
    row = (await s.execute(text("""SELECT id,home_club_id,away_club_id FROM matches
        WHERE id=:m AND is_test=TRUE AND competition_type='FRIENDLY' FOR UPDATE"""),
        {'m': int(match_id)})).mappings().first()
    if row is None:
        return {'matches_deleted': 0, 'clubs_deleted': 0}
    await s.execute(text("DELETE FROM matches WHERE id=:m AND is_test=TRUE AND competition_type='FRIENDLY'"),
                    {'m': int(match_id)})
    candidate_ids = [int(row['home_club_id']), int(row['away_club_id'])]
    orphan_ids = (await s.execute(text("""SELECT c.id FROM clubs c
        WHERE c.id=ANY(:ids) AND c.is_test=TRUE
          AND NOT EXISTS (SELECT 1 FROM matches m WHERE m.home_club_id=c.id OR m.away_club_id=c.id)
          AND NOT EXISTS (SELECT 1 FROM league_teams lt WHERE lt.club_id=c.id)
          AND NOT EXISTS (SELECT 1 FROM cup_matches cm WHERE cm.home_club_id=c.id OR cm.away_club_id=c.id OR cm.winner_id=c.id)
          AND NOT EXISTS (SELECT 1 FROM champions_matches cm WHERE cm.home_club_id=c.id OR cm.away_club_id=c.id OR cm.winner_id=c.id)"""),
        {'ids': candidate_ids})).scalars().all()
    clubs_deleted = 0
    if orphan_ids:
        clubs_deleted = int((await s.execute(text('DELETE FROM clubs WHERE id=ANY(:ids) AND is_test=TRUE'),
                                             {'ids': list(map(int, orphan_ids))})).rowcount or 0)
    return {'matches_deleted': 1, 'clubs_deleted': clubs_deleted}


async def clean_test_data(s):
    """Remove isolated test matches and unused test/bot clubs without erasing league/tournament history."""
    await s.execute(text("SELECT pg_advisory_xact_lock(hashtext('football_manager_clean_test_data'))"))

    # Only explicitly tagged test matches are removed. A club being a bot/test
    # club is not by itself permission to remove any real or historical fixture.
    test_match_ids = [int(x) for x in (await s.execute(text("""SELECT id FROM matches
        WHERE is_test=TRUE ORDER BY id FOR UPDATE"""))).scalars().all()]
    match_filter_ids = test_match_ids or [0]
    match_cleanup_queries = (
        'DELETE FROM match_player_stats WHERE match_id=ANY(:ids)',
        'DELETE FROM match_player_instructions WHERE match_id=ANY(:ids)',
        'DELETE FROM match_events WHERE match_id=ANY(:ids)',
        'DELETE FROM match_substitutions WHERE match_id=ANY(:ids)',
        'DELETE FROM match_tactics WHERE match_id=ANY(:ids)',
        'DELETE FROM match_lineups WHERE match_id=ANY(:ids)',
        'DELETE FROM league_match_finalizations WHERE match_id=ANY(:ids)',
        'DELETE FROM club_financial_transactions WHERE match_id=ANY(:ids)',
    )
    for query in match_cleanup_queries:
        await s.execute(text(query), {'ids': match_filter_ids})
    if test_match_ids:
        # cup_matches/champions_matches keep their historical row and lose only
        # the optional match_id FK (ON DELETE SET NULL); those histories survive.
        await s.execute(text('DELETE FROM matches WHERE id=ANY(:ids) AND is_test=TRUE'),
                        {'ids': test_match_ids})

    # Empty throwaway test leagues can be removed, but never touch a league with
    # teams, fixtures, cup records, or an immutable historical season snapshot.
    empty_test_league_ids = [int(x) for x in (await s.execute(text("""SELECT l.id
        FROM leagues l
        WHERE l.is_test=TRUE
          AND NOT EXISTS (SELECT 1 FROM league_teams lt WHERE lt.league_id=l.id)
          AND NOT EXISTS (SELECT 1 FROM matches m WHERE m.league_id=l.id)
          AND NOT EXISTS (SELECT 1 FROM cups cp WHERE cp.league_id=l.id)
          AND NOT EXISTS (SELECT 1 FROM season_league_snapshots sls WHERE sls.league_id=l.id)
          AND NOT EXISTS (SELECT 1 FROM league_history lh WHERE lh.league_id=l.id)
          AND NOT EXISTS (SELECT 1 FROM league_match_finalizations lmf WHERE lmf.league_id=l.id)
        ORDER BY l.id FOR UPDATE"""))).scalars().all()]
    leagues_deleted = 0
    if empty_test_league_ids:
        leagues_deleted = int((await s.execute(text("""DELETE FROM leagues
            WHERE id=ANY(:ids) AND is_test=TRUE
              AND NOT EXISTS (SELECT 1 FROM league_teams lt WHERE lt.league_id=leagues.id)
              AND NOT EXISTS (SELECT 1 FROM matches m WHERE m.league_id=leagues.id)
              AND NOT EXISTS (SELECT 1 FROM cups cp WHERE cp.league_id=leagues.id)
              AND NOT EXISTS (SELECT 1 FROM season_league_snapshots sls WHERE sls.league_id=leagues.id)
              AND NOT EXISTS (SELECT 1 FROM league_history lh WHERE lh.league_id=leagues.id)
              AND NOT EXISTS (SELECT 1 FROM league_match_finalizations lmf WHERE lmf.league_id=leagues.id)"""),
            {'ids': empty_test_league_ids})).rowcount or 0)

    # Remove only unused clubs that are explicitly tagged as test data or are
    # owned by a bot account (negative Telegram ID). Keep participants in any
    # league, match, cup/UCL, or persisted league history untouched.
    candidates = [int(x) for x in (await s.execute(text("""SELECT c.id
        FROM clubs c
        WHERE (c.is_test=TRUE OR EXISTS (
            SELECT 1 FROM users u WHERE u.id=c.owner_user_id AND u.telegram_id<0))
          AND NOT EXISTS (SELECT 1 FROM league_teams lt WHERE lt.club_id=c.id)
          AND NOT EXISTS (SELECT 1 FROM matches m WHERE m.home_club_id=c.id OR m.away_club_id=c.id)
          AND NOT EXISTS (SELECT 1 FROM cup_matches cm
              WHERE cm.home_club_id=c.id OR cm.away_club_id=c.id OR cm.winner_id=c.id)
          AND NOT EXISTS (SELECT 1 FROM cup_participants cp WHERE cp.club_id=c.id)
          AND NOT EXISTS (SELECT 1 FROM champions_matches clm
              WHERE clm.home_club_id=c.id OR clm.away_club_id=c.id OR clm.winner_id=c.id)
          AND NOT EXISTS (SELECT 1 FROM champions_participants chp WHERE chp.club_id=c.id)
          AND NOT EXISTS (SELECT 1 FROM league_history lh WHERE lh.club_id=c.id)
          AND NOT EXISTS (SELECT 1 FROM transfers tr
              WHERE tr.from_club_id=c.id OR tr.to_club_id=c.id)
          AND NOT EXISTS (SELECT 1 FROM transfer_listings tl WHERE tl.seller_club_id=c.id)
          AND NOT EXISTS (SELECT 1 FROM transfer_offers toff WHERE toff.buyer_club_id=c.id)
        ORDER BY c.id FOR UPDATE"""))).scalars().all()]

    club_filter_ids = candidates or [0]
    # Clear orphaned per-match configuration rows that could otherwise block
    # deletion of a genuinely unused club due to legacy rows without a match.
    await s.execute(text('DELETE FROM match_player_instructions WHERE club_id=ANY(:ids)'), {'ids': club_filter_ids})
    await s.execute(text('DELETE FROM match_tactics WHERE club_id=ANY(:ids)'), {'ids': club_filter_ids})
    await s.execute(text('DELETE FROM match_lineups WHERE club_id=ANY(:ids)'), {'ids': club_filter_ids})
    clubs_deleted = 0
    if candidates:
        clubs_deleted = int((await s.execute(text("""DELETE FROM clubs c
            WHERE c.id=ANY(:ids)
              AND (c.is_test=TRUE OR EXISTS (
                  SELECT 1 FROM users u WHERE u.id=c.owner_user_id AND u.telegram_id<0))
              AND NOT EXISTS (SELECT 1 FROM league_teams lt WHERE lt.club_id=c.id)
              AND NOT EXISTS (SELECT 1 FROM matches m WHERE m.home_club_id=c.id OR m.away_club_id=c.id)
              AND NOT EXISTS (SELECT 1 FROM cup_matches cm
                  WHERE cm.home_club_id=c.id OR cm.away_club_id=c.id OR cm.winner_id=c.id)
              AND NOT EXISTS (SELECT 1 FROM cup_participants cp WHERE cp.club_id=c.id)
              AND NOT EXISTS (SELECT 1 FROM champions_matches clm
                  WHERE clm.home_club_id=c.id OR clm.away_club_id=c.id OR clm.winner_id=c.id)
              AND NOT EXISTS (SELECT 1 FROM champions_participants chp WHERE chp.club_id=c.id)
              AND NOT EXISTS (SELECT 1 FROM league_history lh WHERE lh.club_id=c.id)
              AND NOT EXISTS (SELECT 1 FROM transfers tr
                  WHERE tr.from_club_id=c.id OR tr.to_club_id=c.id)
              AND NOT EXISTS (SELECT 1 FROM transfer_listings tl WHERE tl.seller_club_id=c.id)
              AND NOT EXISTS (SELECT 1 FROM transfer_offers toff WHERE toff.buyer_club_id=c.id)"""),
            {'ids': candidates})).rowcount or 0)

    report = {'clubs_deleted': clubs_deleted,
              'leagues_deleted': leagues_deleted,
              'matches_deleted': len(test_match_ids)}
    logger.info('Admin test-data cleanup: clubs=%s leagues=%s matches=%s',
                report['clubs_deleted'], report['leagues_deleted'], report['matches_deleted'])
    return report


async def archive_league(s, league_id, auto_advance=True):
    """Close one league, persist its season table, then advance the world when all leagues are done."""
    league=(await s.execute(text("SELECT id,name,status,season_number FROM leagues WHERE id=:l FOR UPDATE"),{'l':league_id})).mappings().first()
    if not league:
        raise ValueError('Лига не найдена.')
    if league['status'] == 'FINISHED' and league.get('season_number'):
        return int(league['season_number'])
    rows=(await s.execute(text("""SELECT t.club_id,c.name club_name,t.played,t.wins,t.draws,t.losses,
            t.goals_for,t.goals_against,t.points
        FROM league_teams t JOIN clubs c ON c.id=t.club_id
        WHERE t.league_id=:l
        ORDER BY t.points DESC,(t.goals_for-t.goals_against) DESC,t.goals_for DESC,c.id
    """),{'l':league_id})).mappings().all()
    season_number=int(league.get('season_number') or 1)
    if rows:
        for place,row in enumerate(rows,1):
            await s.execute(text("""INSERT INTO league_history
                (league_id,league_name,season_number,club_id,club_name,place,played,wins,draws,losses,goals_for,goals_against,points)
                VALUES(:l,:ln,:sn,:c,:cn,:place,:played,:wins,:draws,:losses,:gf,:ga,:points)
                ON CONFLICT DO NOTHING
            """), {
                'l':league_id,'ln':league['name'],'sn':season_number,'c':row['club_id'],'cn':row['club_name'],
                'place':place,'played':row['played'],'wins':row['wins'],'draws':row['draws'],'losses':row['losses'],
                'gf':row['goals_for'],'ga':row['goals_against'],'points':row['points'],
            })
        await apply_season_reputation(s, league_id)
        await settle_season_prizes(s, league_id)
    await s.execute(text("UPDATE leagues SET status='FINISHED',season_state='SEASON_END',finished_at=now() WHERE id=:l"),{'l':league_id})
    if auto_advance:
        from app.game.season import check_season_complete
        await check_season_complete(s)
    return season_number


async def _fast_play_round_once(s, league_id):
    """Play the current league round instantly; deliberately sends no match notifications."""
    league=(await s.execute(text('SELECT id,current_round,total_rounds,status FROM leagues WHERE id=:l FOR UPDATE'),{'l':league_id})).mappings().first()
    if not league or league['status']!='ACTIVE': raise ValueError('Лига не активна.')
    round_no=int(league['current_round'])
    all_matches=(await s.execute(text('''SELECT m.*,h.name home_name,a.name away_name
        FROM matches m JOIN clubs h ON h.id=m.home_club_id JOIN clubs a ON a.id=m.away_club_id
        WHERE m.league_id=:l AND m.round=:r ORDER BY m.id FOR UPDATE'''),
        {'l':league_id,'r':round_no})).mappings().all()
    if not all_matches: raise ValueError(f'В текущем туре {round_no} нет матчей.')
    if any(m['status']!='SCHEDULED' for m in all_matches):
        raise ValueError('Тур уже начат или частично сыгран; быстрое отыгрывание доступно только для полностью SCHEDULED тура.')
    matches=all_matches
    results=[]
    for m in matches:
        home=await _starting_players(s,m['home_club_id']); away=await _starting_players(s,m['away_club_id'])
        ht=await _club_tactics(s,m['home_club_id']); at=await _club_tactics(s,m['away_club_id'])
        hb=await _club_board(s,m['home_club_id'],home,ht.formation); ab=await _club_board(s,m['away_club_id'],away,at.formation)
        hc=await get_counterplan(s,m['home_club_id'],m['away_club_id']); ac=await get_counterplan(s,m['away_club_id'],m['home_club_id'])
        result=simulate_match(m['home_name'],m['away_name'],home,away,ht,at,m['seed'],hb,ab,hc,ac)
        results.append(await _persist_fast_match_result(s,m,home,away,ht,at,hb,ab,hc,ac,result))
        await asyncio.sleep(0)
    await finalize_live_round(s,league_id,results)
    return round_no,results


async def fast_play_round(s, league_id):
    return await _with_match_lock_retry(
        lambda: _fast_play_round_once(s,league_id),
        s,
        'fast_play_round',
    )


async def fast_play_all(s, league_id):
    """Play every remaining round sequentially without live notifications."""
    rounds=[]
    while True:
        league=(await s.execute(text('SELECT current_round,total_rounds,status FROM leagues WHERE id=:l FOR UPDATE'),{'l':league_id})).mappings().first()
        if not league or league['status']!='ACTIVE' or int(league['current_round'])>int(league['total_rounds']):
            break
        round_no,results=await fast_play_round(s,league_id)
        rounds.append((round_no,results))
        if int(league['current_round'])==int(league['total_rounds']):
            break
    if not rounds: raise ValueError('Нет оставшихся туров.')
    return rounds


async def finalize_live_round(s, league_id, finished):
    """Idempotently settle finished league matches and advance only a terminal round."""
    league=(await s.execute(text("""SELECT id,current_round,total_rounds,status,season_id,season_number
        FROM leagues WHERE id=:l FOR UPDATE"""),{'l':league_id})).mappings().first()
    if not league or league['status']!='ACTIVE':
        return False
    round_no=int(league['current_round'])
    if round_no<1:
        logger.error('Cannot finalize league=%s: invalid current_round=%s',league_id,round_no)
        return False

    # Process only new successful results from the current round. Marker and
    # side effects share the caller transaction, so rollback undoes both.
    for result in (finished or []):
        match_id=int(result['match_id'])
        match=(await s.execute(text("""SELECT id,round,home_club_id,away_club_id,competition_type,status,home_score,away_score
            FROM matches WHERE id=:m AND league_id=:l FOR UPDATE"""),{'m':match_id,'l':league_id})).mappings().first()
        if not match or match['competition_type']!='LEAGUE' or int(match['round'])!=round_no:
            continue
        if match['status']!='FINISHED':
            continue
        marker=(await s.execute(text("""INSERT INTO league_match_finalizations(match_id,league_id,round_no,outcome,diagnostic)
            VALUES(:m,:l,:r,'FINISHED',NULL) ON CONFLICT(match_id) DO NOTHING RETURNING match_id"""),
            {'m':match_id,'l':league_id,'r':round_no})).scalar_one_or_none()
        if marker is None:
            continue
        h=int(result.get('home_score') if result.get('home_score') is not None else (match['home_score'] or 0))
        a=int(result.get('away_score') if result.get('away_score') is not None else (match['away_score'] or 0))
        if h>a:
            points=((match['home_club_id'],1,0,0,3),(match['away_club_id'],0,0,1,0))
        elif h<a:
            points=((match['home_club_id'],0,0,1,0),(match['away_club_id'],1,0,0,3))
        else:
            points=((match['home_club_id'],0,1,0,1),(match['away_club_id'],0,1,0,1))
        for club_id,wins,draws,losses,pts in points:
            gf=h if club_id==match['home_club_id'] else a
            ga=a if club_id==match['home_club_id'] else h
            await s.execute(text("""UPDATE league_teams SET played=played+1,wins=wins+:w,draws=draws+:d,
                losses=losses+:ld,goals_for=goals_for+:gf,goals_against=goals_against+:ga,points=points+:p
                WHERE league_id=:l AND club_id=:c"""),
                {'w':wins,'d':draws,'ld':losses,'gf':gf,'ga':ga,'p':pts,'l':league_id,'c':club_id})
        await settle_match_economy(s,league_id,match_id,match['home_club_id'],match['away_club_id'])

    # FAILED is terminal for progression only. It gets one technical history
    # marker/log line, but no standings, economy, reward, or player-result stats.
    failed_rows=(await s.execute(text("""SELECT id,round FROM matches
        WHERE league_id=:l AND competition_type='LEAGUE' AND round=:r AND status='FAILED'
        ORDER BY id FOR UPDATE"""),{'l':league_id,'r':round_no})).mappings().all()
    for row in failed_rows:
        marker=(await s.execute(text("""INSERT INTO league_match_finalizations(match_id,league_id,round_no,outcome,diagnostic)
            VALUES(:m,:l,:r,'TECHNICAL_FAILED','Match failed technically; no result was applied')
            ON CONFLICT(match_id) DO NOTHING RETURNING match_id"""),
            {'m':int(row['id']),'l':league_id,'r':round_no})).scalar_one_or_none()
        if marker is not None:
            await s.execute(text('DELETE FROM match_player_stats WHERE match_id=:m'), {'m':int(row['id'])})
            await s.execute(text('UPDATE matches SET home_score=NULL,away_score=NULL WHERE id=:m'), {'m':int(row['id'])})
            logger.error('League match technically failed: league=%s round=%s match=%s; no standings/economy/player-result stats applied',
                         league_id,round_no,int(row['id']))

    # Inspect every current-round match, independent of the worker result list.
    round_rows=(await s.execute(text("""SELECT id,status FROM matches
        WHERE league_id=:l AND competition_type='LEAGUE' AND round=:r ORDER BY id FOR UPDATE"""),
        {'l':league_id,'r':round_no})).mappings().all()
    if not round_rows:
        future=int((await s.execute(text("""SELECT COUNT(*) FROM matches
            WHERE league_id=:l AND competition_type='LEAGUE' AND round>:r"""),
            {'l':league_id,'r':round_no})).scalar_one())
        nonterminal=int((await s.execute(text("""SELECT COUNT(*) FROM matches
            WHERE league_id=:l AND competition_type='LEAGUE' AND status NOT IN ('FINISHED','FAILED')"""),
            {'l':league_id})).scalar_one())
        if round_no>=int(league['total_rounds']) and future==0 and nonterminal==0:
            logger.warning('League schedule exhausted at final round without round rows: league=%s round=%s total_rounds=%s; closing season',
                           league_id,round_no,league['total_rounds'])
            prizes=await settle_season_prizes(s,league_id)
            for club_id,position,amount in prizes:
                await queue_club_notification(s,club_id,'TOURNAMENT','🏆 Сезон завершён',
                    f'Вы заняли {position}-е место. Призовые: €{amount:,}.',f'season-finished:{league_id}:{club_id}')
                await inbox_send_message(club_id,'🏆 Сезон завершён',f'Вы заняли {position}-е место. Призовые: €{amount:,}.',s)
            await archive_league(s,league_id)
            return True
        logger.error('Damaged league schedule; refusing to advance: league=%s season_id=%s round=%s total_rounds=%s round_matches=0 future_rounds=%s nonterminal_matches=%s',
                     league_id,league.get('season_id'),round_no,league['total_rounds'],future,nonterminal)
        return False

    nonterminal=[row for row in round_rows if row['status'] not in ('FINISHED','FAILED')]
    if nonterminal:
        logger.info('League round still in progress: league=%s round=%s nonterminal=%s statuses=%s',
                    league_id,round_no,len(nonterminal),','.join(sorted({str(r['status']) for r in nonterminal})))
        return False

    if round_no>=int(league['total_rounds']):
        future=int((await s.execute(text("""SELECT COUNT(*) FROM matches
            WHERE league_id=:l AND competition_type='LEAGUE' AND round>:r"""),
            {'l':league_id,'r':round_no})).scalar_one())
        if future:
            logger.error('League schedule exceeds configured final round: league=%s current_round=%s total_rounds=%s future_matches=%s; refusing to close',
                         league_id,round_no,league['total_rounds'],future)
            return False

    # The round is now terminal. Run the AI transfer cycle once per successful
    # round transition, not once per worker poll or partial match completion.
    settled_clubs=(await s.execute(text("""SELECT home_club_id,away_club_id FROM matches
        WHERE league_id=:l AND competition_type='LEAGUE' AND round=:r AND status='FINISHED'"""),
        {'l':league_id,'r':round_no})).all()
    round_club_ids=sorted({int(cid) for row in settled_clubs for cid in row if cid is not None})
    if round_club_ids:
        await _run_ai_transfer_cycle(s,round_club_ids)

    if round_no>=int(league['total_rounds']):
        prizes=await settle_season_prizes(s,league_id)
        for club_id,position,amount in prizes:
            await queue_club_notification(s,club_id,'TOURNAMENT','🏆 Сезон завершён',
                f'Вы заняли {position}-е место. Призовые: €{amount:,}.',f'season-finished:{league_id}:{club_id}')
            await inbox_send_message(club_id,'🏆 Сезон завершён',f'Вы заняли {position}-е место. Призовые: €{amount:,}.',s)
        await archive_league(s,league_id)
    else:
        await s.execute(text("UPDATE leagues SET current_round=:n,status='ACTIVE' WHERE id=:l AND current_round=:r"),
                        {'n':round_no+1,'l':league_id,'r':round_no})
        logger.info('Advanced league round: league=%s from_round=%s to_round=%s',league_id,round_no,round_no+1)
    return True


async def deliver_due_notifications(bot, s, limit=50):
    """Claim due notifications, send them concurrently, then persist results."""
    await cancel_stale_match_start_notifications(s)
    ids = (await s.execute(text("""SELECT n.id
        FROM notifications n
        WHERE n.sent_at IS NULL
          AND n.cancelled_at IS NULL
          AND n.failed_at IS NULL
          AND n.deliver_at<=now()
          AND (n.next_attempt_at IS NULL OR n.next_attempt_at<=now())
          AND (n.processing_at IS NULL OR n.processing_at < now()-interval '2 minutes')
        ORDER BY COALESCE(n.next_attempt_at,n.deliver_at),n.id
        LIMIT :n
        FOR UPDATE SKIP LOCKED"""), {'n': limit})).scalars().all()
    if not ids:
        return 0

    rows = (await s.execute(text("""SELECT n.id,n.user_id,n.category,n.title,n.body,n.dedupe_key,n.created_at,u.telegram_id
        FROM notifications n JOIN users u ON u.id=n.user_id
        LEFT JOIN notification_settings ns ON ns.user_id=n.user_id
        WHERE n.id=ANY(:ids)
          AND n.sent_at IS NULL AND n.cancelled_at IS NULL AND n.failed_at IS NULL
          AND CASE n.category
                WHEN 'MATCH' THEN COALESCE(ns.match,TRUE)
                WHEN 'INJURY' THEN COALESCE(ns.injury,TRUE)
                WHEN 'TRANSFER' THEN COALESCE(ns.transfer,TRUE)
                WHEN 'FINANCE' THEN COALESCE(ns.finance,TRUE)
                WHEN 'DEVELOPMENT' THEN COALESCE(ns.development,TRUE)
                WHEN 'DISCIPLINE' THEN COALESCE(ns.discipline,TRUE)
                WHEN 'MORALE' THEN COALESCE(ns.morale,TRUE)
                WHEN 'TOURNAMENT' THEN COALESCE(ns.tournament,TRUE)
                ELSE FALSE
              END
        ORDER BY COALESCE(n.next_attempt_at,n.deliver_at),n.id"""), {'ids': ids})).mappings().all()

    row_ids={r['id'] for r in rows}
    if row_ids:
        await s.execute(text("""UPDATE notifications
            SET processing_at=now(),delivery_attempts=delivery_attempts+1
            WHERE id=ANY(:ids) AND sent_at IS NULL AND cancelled_at IS NULL AND failed_at IS NULL"""), {'ids': list(row_ids)})
    disabled_ids=[i for i in ids if i not in row_ids]
    if disabled_ids:
        await s.execute(text("""UPDATE notifications SET processing_at=NULL
            WHERE id=ANY(:ids) AND sent_at IS NULL AND cancelled_at IS NULL AND failed_at IS NULL"""), {'ids': disabled_ids})
    await s.commit()

    def _priority(row):
        key=str(row.get('dedupe_key') or '')
        category=str(row.get('category') or '')
        return key.startswith(('match-start-10:','match-started:','match-finished:','live-event:'))

    # Group non-critical notifications created in the same minute per user.
    # Match kickoff/start/end and GOAL/RED stay as separate messages.
    grouped=[]
    by_bucket=defaultdict(list)
    for row in rows:
        created=row.get('created_at')
        bucket=created.replace(second=0,microsecond=0) if created is not None else None
        if _priority(row) or bucket is None:
            grouped.append((f"row:{row['id']}",[row]))
        else:
            by_bucket[(row['telegram_id'],bucket)].append(row)
    for key,bucket_rows in by_bucket.items():
        if len(bucket_rows) < 3:
            grouped.extend((f"row:{r['id']}",[r]) for r in bucket_rows)
        else:
            grouped.append((f"group:{key[0]}:{key[1].isoformat()}",bucket_rows))

    async def _send(group):
        rows_for_send=group
        row=rows_for_send[0]
        try:
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            markup=None
            if len(rows_for_send)==1:
                key=str(row.get('dedupe_key') or '')
                if key.startswith('match-start-10:') or key.startswith('match-started:'):
                    parts=key.split(':')
                    mid=parts[4] if key.startswith('match-start-10:') else parts[1]
                    markup=InlineKeyboardMarkup(inline_keyboard=[
                        [
                            InlineKeyboardButton(text='📺 Смотреть матч',callback_data=f'match:watch:{mid}'),
                            InlineKeyboardButton(text='🔍 Обзор матча',callback_data=f'match:preview:{mid}')
                        ],
                        [InlineKeyboardButton(text='🏠 Главное меню',callback_data='menu:main')]
                    ])
                elif key.startswith('live-event:') and row.get('title')=='⚽ ГОЛ':
                    event_id=key.split(':',1)[1]
                    match_id=(await s.execute(text('SELECT match_id FROM match_events WHERE id=:e'),{'e':event_id})).scalar_one_or_none()
                    if match_id:
                        markup=InlineKeyboardMarkup(inline_keyboard=[[
                            InlineKeyboardButton(text='📺 К матчу',callback_data=f'match:watch:{match_id}'),
                            InlineKeyboardButton(text='🏠 Главное меню',callback_data='menu:main')
                        ]])
                elif key.startswith('match-finished:'):
                    mid=key.split(':',1)[1]
                    markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='📊 Полный отчёт',callback_data=f'match:watch:report:{mid}')]])
                elif key.startswith('season-finished:'):
                    markup=InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(text='🏆 Таблица',callback_data='league:table'),
                        InlineKeyboardButton(text='🏠 Главное меню',callback_data='menu:main')
                    ]])
            if len(rows_for_send)==1:
                text_value=f"<b>{row['title']}</b>\n{str(row['body'] or '').replace(r'\n','\n')}"
            else:
                lines=['<b>📋 Уведомления:</b>']
                for item in rows_for_send:
                    body=str(item['body'] or '').replace(r'\n','\n').replace('\n',' ')
                    lines.append(f"• {item['title']}: {body}")
                text_value='\n'.join(lines)
            await telegram_rate_limiter.acquire()
            try:
                await bot.send_message(row['telegram_id'],text_value,reply_markup=markup)
            except TelegramRetryAfter as exc:
                await asyncio.sleep(float(exc.retry_after))
                await telegram_rate_limiter.acquire()
                await bot.send_message(row['telegram_id'],text_value,reply_markup=markup)
            except TelegramAPIError as exc:
                error_code=getattr(exc,'error_code',None)
                status_code=getattr(exc,'status_code',None)
                retry_after=getattr(exc,'retry_after',None)
                if error_code == 429 or status_code == 429 or retry_after is not None:
                    delay=float(retry_after or 1)
                    await asyncio.sleep(delay)
                    await telegram_rate_limiter.acquire()
                    await bot.send_message(row['telegram_id'],text_value,reply_markup=markup)
                else:
                    raise
            return [r['id'] for r in rows_for_send],None
        except Exception as exc:
            return [r['id'] for r in rows_for_send],exc

    # Telegram I/O is the expensive part. Run the claimed sends concurrently so
    # a burst of live events cannot serialize 20-50 network round trips.
    results=await asyncio.gather(*(_send(items) for _,items in grouped))
    delivered=0
    for row_ids,exc in results:
        for row_id in row_ids:
            if exc is not None:
                attempts=(await s.execute(text('SELECT delivery_attempts FROM notifications WHERE id=:id'), {'id':row_id})).scalar_one_or_none() or 1
                delay=min(300,2 ** min(int(attempts),8))
                if int(attempts) >= 8:
                    await s.execute(text("""UPDATE notifications
                        SET processing_at=NULL,failed_at=now(),next_attempt_at=NULL,last_error=:err
                        WHERE id=:id AND sent_at IS NULL AND cancelled_at IS NULL"""), {'id':row_id,'err':str(exc)[:500]})
                else:
                    await s.execute(text("""UPDATE notifications
                        SET processing_at=NULL,next_attempt_at=now()+make_interval(secs=>:delay),last_error=:err
                        WHERE id=:id AND sent_at IS NULL AND cancelled_at IS NULL AND failed_at IS NULL"""), {'id':row_id,'delay':delay,'err':str(exc)[:500]})
                continue
            await s.execute(text("""UPDATE notifications
                SET sent_at=now(),processing_at=NULL,last_error=NULL
                WHERE id=:id AND sent_at IS NULL"""), {'id':row_id})
            delivered+=1
    await s.commit()
    return delivered


# --- Notifications ---------------------------------------------------------
NOTIFICATION_CATEGORIES = {
    'MATCH': 'match', 'INJURY': 'injury', 'TRANSFER': 'transfer',
    'FINANCE': 'finance', 'DEVELOPMENT': 'development', 'DISCIPLINE': 'discipline',
    'MORALE': 'morale', 'TOURNAMENT': 'tournament',
}

async def queue_notification(s, user_id, category, title, body, dedupe_key=None, deliver_at=None):
    if category not in NOTIFICATION_CATEGORIES:
        raise ValueError('Unknown notification category.')
    pref=NOTIFICATION_CATEGORIES[category]
    await s.execute(text(f'''INSERT INTO notification_settings(user_id) VALUES(:u) ON CONFLICT DO NOTHING'''), {'u':user_id})
    enabled=(await s.execute(text(f'SELECT {pref} FROM notification_settings WHERE user_id=:u'), {'u':user_id})).scalar_one()
    if not enabled: return None
    row=(await s.execute(text('''INSERT INTO notifications(user_id,category,title,body,dedupe_key,deliver_at)
        VALUES(:u,:c,:t,:b,:d,COALESCE(CAST(:at AS timestamptz),now()))
        ON CONFLICT(user_id,dedupe_key) WHERE dedupe_key IS NOT NULL DO NOTHING
        RETURNING id'''), {'u':user_id,'c':category,'t':title,'b':body,'d':dedupe_key,'at':deliver_at})).first()
    return row.id if row else None

async def queue_club_notification(s, club_id, category, title, body, dedupe_key=None, deliver_at=None):
    owner=(await s.execute(text('''SELECT u.id,u.telegram_id FROM clubs c JOIN users u ON u.id=c.owner_user_id WHERE c.id=:c'''), {'c':club_id})).mappings().first()
    if not owner or int(owner['telegram_id']) < 0:
        return None
    return await queue_notification(s, owner['id'], category, title, body, dedupe_key, deliver_at)

async def notification_list(s, user_id, limit=20):
    return (await s.execute(text('''SELECT id,category,title,body,created_at,read_at
        FROM notifications WHERE user_id=:u AND deliver_at<=now() AND sent_at IS NOT NULL AND cancelled_at IS NULL ORDER BY created_at DESC,id DESC LIMIT :n'''), {'u':user_id,'n':limit})).mappings().all()

async def notification_unread_count(s, user_id):
    return int((await s.execute(text('SELECT count(*) FROM notifications WHERE user_id=:u AND read_at IS NULL AND deliver_at<=now() AND sent_at IS NOT NULL AND cancelled_at IS NULL'), {'u':user_id})).scalar_one())

async def mark_notifications_read(s, user_id):
    await s.execute(text('UPDATE notifications SET read_at=COALESCE(read_at,now()) WHERE user_id=:u AND read_at IS NULL AND deliver_at<=now() AND sent_at IS NOT NULL AND cancelled_at IS NULL'), {'u':user_id})

async def set_notification_preference(s, user_id, category, enabled):
    if category not in NOTIFICATION_CATEGORIES: raise ValueError('Unknown notification category.')
    await s.execute(text('INSERT INTO notification_settings(user_id) VALUES(:u) ON CONFLICT DO NOTHING'), {'u':user_id})
    col=NOTIFICATION_CATEGORIES[category]
    enabled=bool(enabled)
    await s.execute(text(f'UPDATE notification_settings SET {col}=:v WHERE user_id=:u'), {'v':enabled,'u':user_id})
    if not enabled:
        await s.execute(text('''UPDATE notifications SET cancelled_at=COALESCE(cancelled_at,now()),processing_at=NULL
            WHERE user_id=:u AND category=:c AND sent_at IS NULL AND failed_at IS NULL'''), {'u':user_id,'c':category})

async def notification_preferences(s, user_id):
    await s.execute(text('INSERT INTO notification_settings(user_id) VALUES(:u) ON CONFLICT DO NOTHING'), {'u':user_id})
    return (await s.execute(text('SELECT * FROM notification_settings WHERE user_id=:u'), {'u':user_id})).mappings().first()
