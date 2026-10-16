from secrets import token_urlsafe
import asyncio
import json
from datetime import datetime, timezone, timedelta
from collections import defaultdict
import re
import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)
from app.game.engine import simulate_match, expected_goals
from app.game.ratings import player_rating
from app.game.tactics import Tactics, Style
from app.game.league import round_robin
from app.game.board import FORMATION_SLOTS
from app.game.positions import POSITION_LABELS_RU
from app.models import Player
from app.game.economy import calculate_match_economy, sponsor_income, wage_bill, prize_money, stadium_capacity, TICKET_MIN, TICKET_MAX

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
    return 'Player'


def row_player(r):
    talents=r.get('talents') or []
    if isinstance(talents,str):
        import json; talents=json.loads(talents)
    return Player(*[r[x] for x in ['id','first_name','last_name','position','age','pace','shooting','passing','dribbling','defending','physical','stamina','mental']],
                  fitness=r.get('fitness',100), form=r.get('form',0), suspended=bool(r.get('suspension_matches',0)), talents=tuple(talents), morale=r.get('morale',70))


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


async def create_test_bot_club(s, name, admin_id=None):
    """Create an admin-only test club with a synthetic negative Telegram owner."""
    name=' '.join(str(name or '').split())
    if not 3 <= len(name) <= 30:
        raise ValueError('Название клуба должно содержать 3–30 символов.')
    await s.execute(text("SELECT pg_advisory_xact_lock(hashtext('football-manager-test-club'))"))
    min_id=(await s.execute(text('SELECT MIN(telegram_id) FROM users WHERE telegram_id<0'))).scalar_one_or_none()
    telegram_id=(int(min_id) if min_id is not None else 0)-1
    owner_id=(await s.execute(text("""INSERT INTO users(telegram_id,username,first_name)
        VALUES(:tg,:username,:first_name) RETURNING id"""),
        {'tg':telegram_id,'username':f'bot_{telegram_id}','first_name':f'{name} Bot'})).scalar_one()
    club_id=(await s.execute(text("""INSERT INTO clubs(owner_user_id,name,short_name,stadium_name,budget,fans,reputation,ticket_price)
        VALUES(:u,:n,UPPER(SUBSTRING(:n FROM 1 FOR 4)),:stadium,20000000,5000,1,20)
        RETURNING id"""),
        {'u':owner_id,'n':name,'stadium':f'{name} Arena'})).scalar_one()
    await ensure_squad(s,club_id)
    await s.execute(text('INSERT INTO club_training(club_id) VALUES(:c) ON CONFLICT DO NOTHING'),{'c':club_id})
    await s.execute(text('INSERT INTO club_tactics(club_id) VALUES(:c) ON CONFLICT DO NOTHING'),{'c':club_id})
    await _starting_players(s,club_id)
    if admin_id is not None:
        await s.execute(text('''INSERT INTO admin_actions_log(admin_user_id,action,target_type,target_id,payload)
            VALUES(:u,:a,:t,:i,:p)'''), {'u':admin_id,'a':'test_bot_club_create','t':'club','i':club_id,
            'p':json.dumps({'name':name,'telegram_id':telegram_id},ensure_ascii=False)})
    return club_id,telegram_id

async def create_league(s, creator_user_id, name, max_teams=8):
    club=(await s.execute(text('SELECT id FROM clubs WHERE owner_user_id=:u'),{'u':creator_user_id})).scalar_one_or_none()
    if not club: raise ValueError('Create a club first.')
    await ensure_squad(s,club)
    code=token_urlsafe(6).upper().replace('-','').replace('_','')[:8]
    row=(await s.execute(text('''INSERT INTO leagues(name,invite_code,creator_user_id,max_teams,total_rounds)
        VALUES(:n,:code,:u,:m,:r) RETURNING id'''),
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
    league=(await s.execute(text("SELECT id,max_teams FROM leagues WHERE creator_user_id=:u AND status='WAITING' ORDER BY id DESC LIMIT 1 FOR UPDATE"),{'u':user_id})).mappings().first()
    if not league: raise ValueError('No waiting league found.')
    clubs=[r[0] for r in (await s.execute(text('SELECT club_id FROM league_teams WHERE league_id=:l ORDER BY club_id'),{'l':league['id']})).all()]
    if len(clubs)<4 or len(clubs)%2: raise ValueError('Need an even number of clubs: 4, 6, 8, 10 or 12.')
    fixtures=round_robin(clubs,True)
    # The calendar is the single source of truth for kickoff times.
    # Round 1 starts in 10 minutes; subsequent rounds are spaced by 10 minutes,
    # leaving a buffer after the ~8.5-minute live match.
    season_start=datetime.now(timezone.utc)+timedelta(minutes=10)
    for f in fixtures:
        seed=f.round_no*1_000_003+f.home_club_id*1009+f.away_club_id
        kickoff=season_start+timedelta(minutes=(f.round_no-1)*10)
        await s.execute(text('''INSERT INTO matches(league_id,round,home_club_id,away_club_id,seed,started_at,scheduled_at,prestart_notified_at)
            VALUES(:l,:r,:h,:a,:seed,NULL,CAST(:kickoff AS timestamptz),NULL)'''),
            {'l':league['id'],'r':f.round_no,'h':f.home_club_id,'a':f.away_club_id,'seed':seed,'kickoff':kickoff})
        # Match ID is needed by the live-view button. Resolve it after the insert.
        match_id=(await s.execute(text('SELECT id FROM matches WHERE league_id=:l AND round=:r AND home_club_id=:h AND away_club_id=:a ORDER BY id DESC LIMIT 1'),
            {'l':league['id'],'r':f.round_no,'h':f.home_club_id,'a':f.away_club_id})).scalar_one()
        for cid in (f.home_club_id, f.away_club_id):
            await queue_club_notification(s,cid,'MATCH','⏰ Ваш матч начнётся через 10 минут',
                'Подготовьтесь к матчу.',
                f'match-start-10:{league["id"]}:{f.round_no}:{cid}:{match_id}',kickoff-timedelta(minutes=10))
    total=max(f.round_no for f in fixtures)
    await s.execute(text("UPDATE leagues SET status='ACTIVE',current_round=1,total_rounds=:r,started_at=CAST(:started AS timestamptz) WHERE id=:l"),
                     {'r':total,'started':season_start,'l':league['id']})
    return league['id'],len(fixtures),total

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



_TEMPLATE_SLOTS = [
    'GK','LB','CB','CB','RB','CM','CM','CM','LW','ST','RW',
    'GK','GK','CB','CB','LB','RB','DM','CM','CM','AM','LW','ST',
]
STARTING_SLOT_RARITIES = tuple(
    'RARE' if i in {2,3,6,7,8,10} else 'EPIC' if i in {5,9} else 'BASE'
    for i in range(23)
)


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
        WHERE cp.club_id=:c AND p.template_id=:t LIMIT 1"""),
        {'c':club_id,'t':template_id})).first() is not None


async def add_copy_to_club(s, club_id, template_id, shirt_number=None):
    template=(await s.execute(text('SELECT market_value,salary FROM players WHERE id=:t AND is_template=TRUE FOR SHARE'), {'t':template_id})).mappings().first()
    if not template:
        raise ValueError('Player template not found.')
    if await club_has_template(s, club_id, template_id):
        comp=int(template['market_value'])//2
        club=(await s.execute(text('SELECT budget FROM clubs WHERE id=:c FOR UPDATE'), {'c':club_id})).mappings().first()
        if not club:
            raise ValueError('Club not found.')
        new_budget=int(club['budget'])+comp
        await s.execute(text('UPDATE clubs SET budget=:b WHERE id=:c'), {'b':new_budget,'c':club_id})
        await s.execute(text("""INSERT INTO club_financial_transactions(
            club_id,category,amount,balance_after,description)
            VALUES(:c,'OTHER',:amount,:balance,:description)"""), {
            'c':club_id,'amount':comp,'balance':new_budget,
            'description':f'Компенсация за повторный выбор шаблона игрока #{template_id}',
        })
        return None, comp
    new_id=await create_player_copy(s, template_id)
    inserted=(await s.execute(text("""INSERT INTO club_players(
        club_id,player_id,template_id,shirt_number,fitness,form,morale,contract_salary,release_clause)
        VALUES(:c,:p,:t,:shirt,100,0,70,:salary,:clause)
        ON CONFLICT (club_id,template_id) WHERE template_id IS NOT NULL DO NOTHING
        RETURNING player_id"""), {
        'c':club_id,'p':new_id,'t':template_id,'shirt':shirt_number,
        'salary':int(template['salary']),'clause':int(template['market_value'])*2,
    })).scalar_one_or_none()
    if inserted is None:
        await s.execute(text('DELETE FROM players WHERE id=:p AND is_template=FALSE'), {'p':new_id})
        comp=int(template['market_value'])//2
        club=(await s.execute(text('SELECT budget FROM clubs WHERE id=:c FOR UPDATE'), {'c':club_id})).mappings().first()
        if not club:
            raise ValueError('Club not found.')
        new_budget=int(club['budget'])+comp
        await s.execute(text('UPDATE clubs SET budget=:b WHERE id=:c'), {'b':new_budget,'c':club_id})
        await s.execute(text("""INSERT INTO club_financial_transactions(
            club_id,category,amount,balance_after,description)
            VALUES(:c,'OTHER',:amount,:balance,:description)"""), {
            'c':club_id,'amount':comp,'balance':new_budget,
            'description':f'Компенсация за повторный выбор шаблона игрока #{template_id}',
        })
        return None, comp
    return int(inserted), 0


async def _template_usage_counts(s, club_id):
    rows=(await s.execute(text("""SELECT p.template_id,COUNT(*) AS uses
        FROM club_players cp JOIN players p ON p.id=cp.player_id
        WHERE cp.club_id<>:c AND p.template_id IS NOT NULL
        GROUP BY p.template_id"""), {'c':club_id})).all()
    return {int(r.template_id):int(r.uses) for r in rows}


async def _pick_template(s, used_templates, rarity_order, positions, usage):
    for rarity in rarity_order:
        rows=(await s.execute(text("""SELECT id FROM players
            WHERE is_template=TRUE AND rarity=:rarity
              AND position=ANY(:positions)
              AND NOT (id=ANY(:used))"""), {
            'rarity':rarity,'positions':list(positions),'used':list(used_templates or [0]),
        })).scalars().all()
        if rows:
            return int(min(rows, key=lambda pid:(usage.get(int(pid),0), int(pid))))
    return None


async def _build_starting_squad_from_templates(s, club_id):
    """Create the 23-player 4-3-3 squad from unique player templates."""
    existing=int((await s.execute(text('SELECT COUNT(*) FROM club_players WHERE club_id=:c'), {'c':club_id})).scalar_one())
    if existing:
        if existing != 23:
            raise ValueError('Club already has a non-standard squad; reset it before rebuilding.')
        return existing
    usage=await _template_usage_counts(s,club_id)
    slots=list(_TEMPLATE_SLOTS)
    # Exact 4-3-3 starting XI occupies the first 11 slots. Two EPIC cards go
    # to key positions; six RARE cards are spread across CB/CM/LW/RW.
    used=set()
    selected=[]
    for idx,pos in enumerate(slots):
        target_rarity=STARTING_SLOT_RARITIES[idx]
        if target_rarity == 'EPIC':
            rarity_order=('EPIC','RARE','BASE')
        elif target_rarity == 'RARE':
            rarity_order=('RARE','EPIC','BASE')
        else:
            rarity_order=('BASE','RARE','EPIC')
        tid=await _pick_template(s,used,rarity_order,{pos},usage)
        if tid is None:
            raise RuntimeError(f'No template available for {pos}.')
        selected.append(tid)
        used.add(tid)
    if len(selected) != 23 or len(set(selected)) != 23:
        raise RuntimeError('Could not build a unique 23-player squad.')
    created_ids=[]
    for shirt,tid in enumerate(selected,1):
        new_id,_=await add_copy_to_club(s,club_id,tid,shirt)
        if new_id is not None:
            created_ids.append(int(new_id))
    if len(created_ids) != 23:
        raise RuntimeError('Could not create all 23 player copies for the squad.')
    await s.execute(text('INSERT INTO club_lineups(club_id,player_ids) VALUES(:c,:ids) ON CONFLICT(club_id) DO UPDATE SET player_ids=EXCLUDED.player_ids'),
                    {'c':club_id,'ids':created_ids[:11]})
    return 23



def build_starting_lineup(first, second):
    if hasattr(first, 'execute'):
        return _build_starting_squad_from_templates(first, second)
    return _build_position_lineup(first, second)


async def full_reset(s, admin_user_id=None):
    """Manually reset all club/season state and rebuild every squad from templates."""
    active_leagues=(await s.execute(text("SELECT id FROM leagues WHERE status='ACTIVE' ORDER BY id FOR UPDATE"))).scalars().all()
    for league_id in active_leagues:
        await archive_league(s, int(league_id))
    for sql in (
        'DELETE FROM match_player_stats','DELETE FROM match_events','DELETE FROM match_substitutions',
        'DELETE FROM match_tactics','DELETE FROM match_lineups','DELETE FROM club_substitution_plans',
        'DELETE FROM club_counterplans','DELETE FROM club_player_instructions','DELETE FROM club_lineups',
        'DELETE FROM club_tactics','DELETE FROM club_training','DELETE FROM player_development_log',
        'DELETE FROM transfer_offers','DELETE FROM transfer_listings','DELETE FROM transfers',
        'DELETE FROM club_players',
        'DELETE FROM league_teams','DELETE FROM matches','DELETE FROM leagues',
        'DELETE FROM club_season_finances','DELETE FROM club_financial_transactions',
        'DELETE FROM players WHERE is_template=FALSE',
        "UPDATE clubs SET budget=2000000,fans=5000,reputation=1,stadium_level=1,ticket_price=20,sponsor_level=1,debt=0",
        'UPDATE players SET is_template=TRUE,template_id=NULL WHERE is_seed=TRUE',
    ):
        await s.execute(text(sql))
    club_ids=[int(r[0]) for r in (await s.execute(text('SELECT id FROM clubs ORDER BY id'))).all()]
    for club_id in club_ids:
        await build_starting_lineup(s,club_id)
        await s.execute(text('INSERT INTO club_training(club_id) VALUES(:c) ON CONFLICT DO NOTHING'), {'c':club_id})
        await s.execute(text('INSERT INTO club_tactics(club_id) VALUES(:c) ON CONFLICT DO NOTHING'), {'c':club_id})
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
    if not exists: raise ValueError("Player is not in your squad.")
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


async def get_counterplan(s, club_id, opponent_club_id):
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


async def record_financial_transaction(s, club_id, category, amount, description, league_id=None, match_id=None, allow_debt=False):
    if match_id is not None:
        existing=(await s.execute(text('SELECT balance_after FROM club_financial_transactions WHERE match_id=:m AND club_id=:c AND category=:cat LIMIT 1'), {'m':match_id,'c':club_id,'cat':category})).scalar_one_or_none()
        if existing is not None:
            return int(existing)
    club=(await s.execute(text('SELECT budget,COALESCE(debt,0) debt FROM clubs WHERE id=:c FOR UPDATE'), {'c':club_id})).mappings().first()
    if not club: raise ValueError('Club not found.')
    new_balance=int(club['budget'])+int(amount)
    new_debt=int(club['debt'])
    if new_balance<0:
        if not allow_debt:
            raise ValueError('Transaction would make the club budget negative.')
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
    await record_financial_transaction(s,home_club_id,'TICKETS',home.ticket_revenue,
        f'Dомашний матч: {home.attendance:,} зрителей × €{home.ticket_price}',league_id,match_id)
    await record_financial_transaction(s,home_club_id,'STADIUM',-home.stadium_cost,
        f'Содержание стадиона и матчдэй: {home.attendance:,} зрителей',league_id,match_id,True)
    for cid in (home_club_id,away_club_id):
        c=clubs[cid]
        sponsor=sponsor_income(c['sponsor_level'],c['reputation'])
        await record_financial_transaction(s,cid,'SPONSOR',sponsor,'Спонсорский доход за тур',league_id,match_id)
        salaries=(await s.execute(text('SELECT COALESCE(cp.contract_salary,p.salary) FROM club_players cp JOIN players p ON p.id=cp.player_id WHERE cp.club_id=:c'),{'c':cid})).scalars().all()
        wages=wage_bill(salaries)
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
    if not club: raise ValueError('Club not found.')
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
    if (await s.execute(text("SELECT 1 FROM club_financial_transactions WHERE league_id=:l AND category='PRIZE' LIMIT 1"), {'l':league_id})).first():
        return []
    rows=(await s.execute(text('''SELECT club_id,ROW_NUMBER() OVER (ORDER BY points DESC,(goals_for-goals_against) DESC,goals_for DESC,club_id) position,
        COUNT(*) OVER () teams FROM league_teams WHERE league_id=:l'''),{'l':league_id})).mappings().all()
    out=[]
    for r in rows:
        amount=prize_money(r['position'],r['teams'])
        await record_financial_transaction(s,r['club_id'],'PRIZE',amount,f'Призовые за {r["position"]}-е место',league_id)
        out.append((r['club_id'],r['position'],amount))
    return out


async def market_players(s, limit=10):
    return (await s.execute(text(f'''SELECT p.id,p.first_name,p.last_name,p.position,p.age,p.market_value,p.potential,
        p.pace,p.shooting,p.passing,p.dribbling,p.defending,p.physical,p.stamina,p.mental
        FROM players p LEFT JOIN club_players cp ON cp.player_id=p.id
        WHERE cp.player_id IS NULL ORDER BY p.market_value DESC,p.id LIMIT :n'''),{'n':limit})).mappings().all()

async def buy_player(s, club_id, player_id):
    player=(await s.execute(text('''SELECT p.id,p.first_name,p.last_name,p.market_value,COALESCE(p.template_id,p.id) AS source_template_id FROM players p
        WHERE p.id=:p AND NOT EXISTS(SELECT 1 FROM club_players cp WHERE cp.player_id=p.id) FOR UPDATE'''),{'p':player_id})).mappings().first()
    if not player: raise ValueError('Player is no longer available on the market.')
    club=(await s.execute(text('SELECT budget FROM clubs WHERE id=:c FOR UPDATE'),{'c':club_id})).mappings().first()
    if not club: raise ValueError('Club not found.')
    duplicate=(await s.execute(text('''SELECT 1 FROM club_players cp JOIN players p ON p.id=cp.player_id
        WHERE cp.club_id=:c AND COALESCE(p.template_id,p.id)=:t LIMIT 1'''),
        {'c':club_id,'t':int(player['source_template_id'])})).first()
    if duplicate: raise ValueError('У вас уже есть такой игрок.')
    price=int(player['market_value'])
    if club['budget']<price: raise ValueError(f'Not enough budget. Need €{price:,}.')
    await record_financial_transaction(s,club_id,'TRANSFER_OUT',-price,f'Покупка {display_player_name(player.get("first_name"),player.get("last_name"))} за €{price:,}')
    await s.execute(text('INSERT INTO club_players(club_id,player_id,template_id) SELECT :c,:p,template_id FROM players WHERE id=:p'),{'c':club_id,'p':player_id})
    await s.execute(text('INSERT INTO transfers(player_id,from_club_id,to_club_id,price) VALUES(:p,NULL,:c,:price)'),{'p':player_id,'c':club_id,'price':price})
    return player,price

async def sell_player(s, club_id, player_id):
    row=(await s.execute(text('''SELECT p.id,p.first_name,p.last_name,p.market_value,cp.shirt_number
        FROM club_players cp JOIN players p ON p.id=cp.player_id WHERE cp.club_id=:c AND cp.player_id=:p FOR UPDATE'''),
        {'c':club_id,'p':player_id})).mappings().first()
    if not row: raise ValueError('This player is not in your squad.')
    count=(await s.execute(text('SELECT count(*) FROM club_players WHERE club_id=:c'),{'c':club_id})).scalar_one()
    if count<=18: raise ValueError('You must keep at least 18 players in the squad.')
    price=max(50_000,int(row['market_value']*0.70))
    await s.execute(text('DELETE FROM club_players WHERE club_id=:c AND player_id=:p'),{'c':club_id,'p':player_id})
    await record_financial_transaction(s,club_id,'TRANSFER_IN',price,f'Продажа {display_player_name(row.get("first_name"),row.get("last_name"))} за €{price:,}')
    await s.execute(text('INSERT INTO transfers(player_id,from_club_id,to_club_id,price) VALUES(:p,:c,NULL,:price)'),{'p':player_id,'c':club_id,'price':price})
    return row,price


def _tactics_json(t):
    return {'formation':t.formation,'style':t.style.value,'tempo':t.tempo,'pressing':t.pressing,'width':t.width,'defensive_line':t.defensive_line,'aggression':t.aggression}

def _tactics_from_json(d):
    return Tactics(formation=d.get('formation','4-3-3'), style=Style(d.get('style','BALANCE')), tempo=int(d.get('tempo',50)), pressing=int(d.get('pressing',50)), width=int(d.get('width',50)), defensive_line=int(d.get('defensive_line',50)), aggression=int(d.get('aggression',50)))

async def schedule_round(s, league_id, round_no, delay_minutes=10):
    league=(await s.execute(text('SELECT id,current_round,status FROM leagues WHERE id=:l FOR UPDATE'),{'l':league_id})).mappings().first()
    if not league or league['status']!='ACTIVE': raise ValueError('League is not active.')
    if league['current_round']!=round_no: raise ValueError(f'Expected round {league["current_round"]}, got {round_no}.')
    ms=(await s.execute(text("SELECT id,home_club_id,away_club_id,status,scheduled_at FROM matches WHERE league_id=:l AND round=:r ORDER BY id FOR UPDATE"),{'l':league_id,'r':round_no})).mappings().all()
    if not ms: raise ValueError('No matches in this round.')
    if any(m['status']=='LIVE' for m in ms): raise ValueError('This round is already live.')
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
    if not league or league['status']!='ACTIVE': raise ValueError('League is not active.')
    if league['current_round']!=round_no: raise ValueError(f'Expected round {league["current_round"]}, got {round_no}.')
    ms=(await s.execute(text("SELECT id,home_club_id,away_club_id,seed,scheduled_at FROM matches WHERE league_id=:l AND round=:r AND status='SCHEDULED' ORDER BY id FOR UPDATE"),{'l':league_id,'r':round_no})).mappings().all()
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
        hs,as_,events=simulate_first_half(home,away,ht,at,m['seed']*2+1,hc,ac,hb,ab)
        kickoff=m.get('scheduled_at') or now
        phase_start=min(kickoff, now)
        full_hx,full_ax=expected_goals(home,away,ht,at,hb,ab,hc,ac)
        first_hx,first_ax=round(full_hx*.48,2),round(full_ax*.48,2)
        await s.execute(text("UPDATE matches SET home_score=0,away_score=0,home_xg=0,away_xg=0,home_possession=50,away_possession=50,home_halftime_score=:hs,away_halftime_score=:as_,current_minute=0,status='LIVE',halftime_locked=FALSE,halftime_data=:data,second_half_data='{}'::jsonb,live_phase='FIRST_HALF',phase_started_at=CAST(:phase_start AS timestamptz),halftime_ends_at=NULL,second_half_started_at=NULL,live_event_cursor=0,started_at=COALESCE(started_at,CAST(:phase_start AS timestamptz)),finished_at=NULL WHERE id=:m"),{'hs':hs,'as_':as_,'m':m['id'],'phase_start':phase_start,'data':json.dumps({'home_tactics':_tactics_json(ht),'away_tactics':_tactics_json(at),'events':[e.__dict__ for e in events], 'home_board':[(z.x,z.y,z.role,z.instruction) for z in hb.slots], 'away_board':[(z.x,z.y,z.role,z.instruction) for z in ab.slots], 'home_players':[p.id for p in home], 'away_players':[p.id for p in away], 'first_half_xg':[first_hx,first_ax], 'full_xg':[round(full_hx,2),round(full_ax,2)]})})
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




# --- Real-time live match lifecycle ---
LIVE_SECONDS_PER_MINUTE = 5
LIVE_HALFTIME_SECONDS = 60
LIVE_FIRST_HALF_SECONDS = 45 * LIVE_SECONDS_PER_MINUTE
LIVE_SECOND_HALF_SECONDS = 45 * LIVE_SECONDS_PER_MINUTE

async def _live_match_rows(s, league_id=None):
    q="SELECT * FROM matches WHERE status='LIVE'"
    params={}
    if league_id is not None:
        q += " AND league_id=:l"; params['l']=league_id
    q += " ORDER BY id LIMIT 25 FOR UPDATE SKIP LOCKED"
    return (await s.execute(text(q), params)).mappings().all()

async def _prepare_second_half_live(s, match, phase_start_at=None):
    from app.game.engine import simulate_second_half
    original_home=await _starting_players(s,match['home_club_id']); original_away=await _starting_players(s,match['away_club_id'])
    # First-half red cards and injuries affect the second-half active XI.
    unavailable=(await s.execute(text('''SELECT club_id,player_id,event_type,minute FROM match_events
        WHERE match_id=:m AND minute<=45 AND event_type IN ('RED','INJURY') AND player_id IS NOT NULL'''),
        {'m':match['id']})).mappings().all()
    unavailable_by_club={match['home_club_id']:set(),match['away_club_id']:set()}
    first_half_exits=[]
    for e in unavailable:
        unavailable_by_club.setdefault(e['club_id'],set()).add(e['player_id'])
        first_half_exits.append((int(e['minute']),int(e['player_id']),e['event_type'],'HOME' if e['club_id']==match['home_club_id'] else 'AWAY'))
    home=[p for p in original_home if p.id not in unavailable_by_club.get(match['home_club_id'],set())]
    away=[p for p in original_away if p.id not in unavailable_by_club.get(match['away_club_id'],set())]
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
    hc=await get_counterplan(s,match['home_club_id'],match['away_club_id']); ac=await get_counterplan(s,match['away_club_id'],match['home_club_id'])
    hs,as_,events=simulate_second_half(home,away,ht,at,match['seed']*2+2,substitutions,hc,ac,hb,ab)
    hname=(await s.execute(text('SELECT name FROM clubs WHERE id=:c'),{'c':match['home_club_id']})).scalar_one()
    aname=(await s.execute(text('SELECT name FROM clubs WHERE id=:c'),{'c':match['away_club_id']})).scalar_one()
    # As in the first half, keep the generated event timeline in phase state
    # and materialize only reached minutes in advance_live_matches().
    final_h=(match['home_halftime_score'] or 0)+hs; final_a=(match['away_halftime_score'] or 0)+as_
    data={'home':match['home_club_id'],'away':match['away_club_id'],'home_name':hname,'away_name':aname,'final_h':final_h,'final_a':final_a,
          'home_players':[p.id for p in original_home],'away_players':[p.id for p in original_away],
          'first_half_exits':first_half_exits,
          'substitutions':[(int(a),int(b),int(c.id),d) for a,b,c,d in substitutions],
          'events':[e.__dict__ for e in events],
          'home_tactics':_tactics_json(ht),'away_tactics':_tactics_json(at)}
    hx,ax=expected_goals(home,away,ht,at)
    first_half_xg=[float(x) for x in (match.get('halftime_data') or {}).get('first_half_xg',[0,0])]
    data['second_half_xg']=[round(hx*.52,2),round(ax*.52,2)]
    data['full_xg']=[round(first_half_xg[0]+hx*.52,2),round(first_half_xg[1]+ax*.52,2)]
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
    existing=(await s.execute(text('''SELECT minute,event_type,club_id,player_id,secondary_player_id,description
        FROM match_events WHERE match_id=:m'''),{'m':match['id']})).mappings().all()
    existing_keys={(int(r['minute']),r['event_type'],r['club_id'],r['player_id'],r['secondary_player_id'],r['description']) for r in existing}
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
        description=str(raw.get('text') or raw.get('description') or '').replace('\\n','\n')
        metadata=raw.get('metadata') or {}
        key=(minute,raw.get('type'),cid,player_id,secondary_id,description)
        if key in existing_keys:
            continue
        await s.execute(text('''INSERT INTO match_events(match_id,minute,event_type,club_id,player_id,secondary_player_id,description,metadata)
            VALUES(:m,:min,:t,:c,:p,:sp,:d,:meta)'''),
            {'m':match['id'],'min':minute,'t':raw.get('type'),'c':cid,'p':player_id,'sp':secondary_id,'d':description,'meta':json.dumps(metadata)})
        existing_keys.add(key)
        inserted+=1
    return inserted


async def _live_score_at_minute(s, match_id, minute, home_club_id, away_club_id):
    rows=(await s.execute(text('''SELECT club_id,count(*) n FROM match_events
        WHERE match_id=:m AND event_type='GOAL' AND minute<=:minute GROUP BY club_id'''),
        {'m':match_id,'minute':minute})).mappings().all()
    home=next((int(r['n']) for r in rows if r['club_id']==home_club_id),0)
    away=next((int(r['n']) for r in rows if r['club_id']==away_club_id),0)
    return home,away


def _live_possession(phase_data, minute):
    home_t=_tactics_from_json(phase_data.get('home_tactics') or {})
    away_t=_tactics_from_json(phase_data.get('away_tactics') or {})
    reached=[e for e in (phase_data.get('events') or []) if int(e.get('minute',0))<=int(minute)]
    home_actions=sum(1 for e in reached if e.get('team')=='HOME' and e.get('type') in {'PASS','CARRY','INTERCEPTION'})
    away_actions=sum(1 for e in reached if e.get('team')=='AWAY' and e.get('type') in {'PASS','CARRY','INTERCEPTION'})
    tactical=(home_t.tempo-away_t.tempo)*0.06+(home_t.pressing-away_t.pressing)*0.035
    flow=(home_actions-away_actions)*0.35
    home=max(35.0,min(65.0,50.0+tactical+flow))
    return round(home,2),round(100.0-home,2)


async def _finish_live_match(s, match):
    data=match['second_half_data'] or {}
    if not data: raise ValueError('Second-half state is missing.')
    second_h,second_a=await _live_score_at_minute(s,match['id'],90,match['home_club_id'],match['away_club_id'])
    final_h=int(match['home_halftime_score'] or 0)+second_h
    final_a=int(match['away_halftime_score'] or 0)+second_a
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
    discipline=(await s.execute(text("SELECT club_id,player_id,event_type FROM match_events WHERE match_id=:m AND event_type IN ('INJURY','RED') AND player_id IS NOT NULL"),{'m':match['id']})).mappings().all()
    for e in discipline:
        pname_row=(await s.execute(text("SELECT first_name,last_name FROM players WHERE id=:p"),{'p':e['player_id']})).mappings().first()
        pname=display_player_name(pname_row.get('first_name'),pname_row.get('last_name')) if pname_row else 'Игрок'
        if e['event_type']=='INJURY':
            await s.execute(text('UPDATE club_players SET is_injured=true,injury_matches=GREATEST(injury_matches,2) WHERE club_id=:c AND player_id=:p'),{'c':e['club_id'],'p':e['player_id']})
            await queue_club_notification(s,e['club_id'],'INJURY','🩹 Игрок получил травму',f'{pname} получил травму. Ожидаемое восстановление — минимум 2 матча.',f'injury:{match["id"]}:{e["player_id"]}')
        elif e['event_type']=='RED':
            await s.execute(text('UPDATE club_players SET suspension_matches=GREATEST(suspension_matches,1) WHERE club_id=:c AND player_id=:p'),{'c':e['club_id'],'p':e['player_id']})
            await queue_club_notification(s,e['club_id'],'DISCIPLINE','🟥 Удаление',f'{pname} получил красную карточку и пропустит следующий матч.',f'red:{match["id"]}:{e["player_id"]}')
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
    await s.execute(text("UPDATE matches SET home_score=:h,away_score=:a,current_minute=90,motm_player_id=:motm,status='FINISHED',live_phase='FINISHED',halftime_locked=FALSE,finished_at=now() WHERE id=:m"),{'h':final_h,'a':final_a,'motm':motm_player_id,'m':match['id']})
    # One compact final notification per owner. Goal details and MOTM are kept
    # here so the delivery layer can attach a single full-report button.
    club_names={match['home_club_id']:data.get('home_name','Home'),match['away_club_id']:data.get('away_name','Away')}
    goal_events=(await s.execute(text('''SELECT e.minute,e.club_id,e.player_id,p.first_name,p.last_name
        FROM match_events e LEFT JOIN players p ON p.id=e.player_id
        WHERE e.match_id=:m AND e.event_type='GOAL' ORDER BY e.minute,e.id'''),{'m':match['id']})).mappings().all()
    goal_lines=[]
    for e in goal_events:
        goal_lines.append(f"• {e['minute']}′ {display_player_name(e.get('first_name'),e.get('last_name'))} ({club_names.get(e['club_id'],'Команда')})")
    motm_text='—'
    if motm_player_id:
        motm_row=(await s.execute(text('SELECT p.first_name,p.last_name,ps.rating FROM players p JOIN match_player_stats ps ON ps.player_id=p.id AND ps.match_id=:m WHERE p.id=:p'),{'m':match['id'],'p':motm_player_id})).mappings().first()
        if motm_row:
            motm_text=f"{display_player_name(motm_row['first_name'],motm_row['last_name'])} ({float(motm_row['rating'] or 0):.1f})"
    result_text=(f"{data.get('home_name','Home')} <b>{final_h}:{final_a}</b> {data.get('away_name','Away')}\n\n"
                  f"⚽ <b>Голы:</b>\n{chr(10).join(goal_lines) if goal_lines else '• Нет голов'}\n\n"
                  f"⭐ <b>MOTM:</b> {motm_text}")
    await queue_club_notification(s,match['home_club_id'],'MATCH','🏁 Матч завершён',result_text,f'match-finished:{match["id"]}')
    await queue_club_notification(s,match['away_club_id'],'MATCH','🏁 Матч завершён',result_text,f'match-finished:{match["id"]}')
    return {'match_id':match['id'],'home':data.get('home_name','Home'),'away':data.get('away_name','Away'),'home_score':final_h,'away_score':final_a}

async def advance_live_matches(s, league_id=None):
    now=datetime.now(timezone.utc)
    rows=await _live_match_rows(s,league_id)
    transitions=[]; finished=[]
    for match in rows:
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
                    {'min':minute,'hs':hg,'as_':ag,'hx':round(float(first_xg[0])*minute/45,2),'ax':round(float(first_xg[1])*minute/45,2),'hp':hp,'ap':ap,'m':match['id']})
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
            wall_target=min(90,45+int(elapsed//LIVE_SECONDS_PER_MINUTE))
            minute=min(90,current+min(3,max(0,wall_target-current)))
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
                    {'min':minute,'hs':home_score,'as_':away_score,'hx':round(float(first_xg[0])+float(second_xg[0])*progress,2),'ax':round(float(first_xg[1])+float(second_xg[1])*progress,2),'hp':hp,'ap':ap,'m':match['id']})
            if minute>=90:
                refreshed=(await s.execute(text('SELECT * FROM matches WHERE id=:m FOR UPDATE'),{'m':match['id']})).mappings().first()
                if refreshed and refreshed['live_phase']=='SECOND_HALF':
                    result=await _finish_live_match(s,refreshed)
                    finished.append(result); transitions.append((match['id'],'FULL_TIME'))
    return transitions,finished


# --- Club management expansion (v2) ---
from app.game.club_management import contract_offer, transfer_quote, training_result, attribute_growth, morale_after_match

async def market_players(s, limit=10, listed_only=False):
    where = "tl.status='LISTED'" if listed_only else "(cp.player_id IS NULL OR tl.status='LISTED')"
    return (await s.execute(text(f'''SELECT p.id,p.first_name,p.last_name,p.position,p.age,p.market_value,p.potential,
        p.pace,p.shooting,p.passing,p.dribbling,p.defending,p.physical,p.stamina,p.mental,p.rarity,
        cp.club_id seller_club_id,tl.id listing_id,tl.asking_price,tl.minimum_price
        FROM players p LEFT JOIN club_players cp ON cp.player_id=p.id
        LEFT JOIN transfer_listings tl ON tl.player_id=p.id AND tl.status='LISTED'
        WHERE {where} ORDER BY COALESCE(tl.asking_price,p.market_value) DESC,p.id LIMIT :n'''),{'n':limit})).mappings().all()

async def list_player_for_transfer(s, club_id, player_id, asking_price=None):
    row=(await s.execute(text('''SELECT p.*,cp.contract_until,cp.contract_salary,cp.form,cp.morale
        FROM club_players cp JOIN players p ON p.id=cp.player_id
        WHERE cp.club_id=:c AND cp.player_id=:p FOR UPDATE'''),{'c':club_id,'p':player_id})).mappings().first()
    if not row: raise ValueError('This player is not in your squad.')
    count=(await s.execute(text('SELECT count(*) FROM club_players WHERE club_id=:c'),{'c':club_id})).scalar_one()
    if count<=18: raise ValueError('You must keep at least 18 players in the squad.')
    existing=(await s.execute(text("SELECT id FROM transfer_listings WHERE player_id=:p AND status='LISTED'"),{'p':player_id})).first()
    if existing: raise ValueError('Player is already listed.')
    import datetime
    years=2
    if row['contract_until']:
        years=max(0,(row['contract_until']-datetime.date.today()).days//365)
    q=transfer_quote(row['market_value'],row['age'],row['form'],years,True)
    ask=max(q.minimum_price,int(asking_price) if asking_price else q.asking_price)
    if ask>q.asking_price*2: raise ValueError('Asking price is too high for the market.')
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
    if not club or club['budget']<amount: raise ValueError(f'Not enough budget for €{amount:,}.')
    oid=(await s.execute(text('INSERT INTO transfer_offers(listing_id,buyer_club_id,amount) VALUES(:l,:c,:a) RETURNING id'),{'l':listing_id,'c':buyer_club_id,'a':amount})).scalar_one()
    player=(await s.execute(text('SELECT first_name,last_name FROM players WHERE id=:p'),{'p':listing['player_id']})).mappings().first()
    await queue_club_notification(s,listing['seller_club_id'],'TRANSFER','📩 Новое трансферное предложение',f"Предложение €{amount:,} за {display_player_name(player.get('first_name'),player.get('last_name'))}. Решение ожидает тебя.",f'transfer-offer:{oid}')
    return oid

async def accept_transfer_offer(s, seller_club_id, offer_id):
    offer=(await s.execute(text('''SELECT o.*,l.player_id,l.seller_club_id,l.minimum_price,l.status listing_status,
        p.first_name,p.last_name,p.market_value
        FROM transfer_offers o JOIN transfer_listings l ON l.id=o.listing_id JOIN players p ON p.id=l.player_id
        WHERE o.id=:o AND o.status='PENDING' FOR UPDATE'''),{'o':offer_id})).mappings().first()
    if not offer or offer['seller_club_id']!=seller_club_id or offer['listing_status']!='LISTED': raise ValueError('You cannot accept this offer.')
    if offer['amount']<offer['minimum_price']: raise ValueError('Offer is below the minimum price.')
    buyer=offer['buyer_club_id']; seller=seller_club_id
    if buyer == seller: raise ValueError('Buyer and seller clubs must differ.')
    locked=(await s.execute(text('SELECT id,budget FROM clubs WHERE id=ANY(:ids) ORDER BY id FOR UPDATE'), {'ids':[buyer,seller]})).mappings().all()
    if len(locked)!=2: raise ValueError('Buyer or seller club not found.')
    count=(await s.execute(text('SELECT count(*) FROM club_players WHERE club_id=:c'),{'c':buyer})).scalar_one()
    if count>=30: raise ValueError('Buyer squad is full.')
    budget=next(int(r['budget']) for r in locked if r['id']==buyer)
    if budget<offer['amount']: raise ValueError('Buyer no longer has enough budget.')
    duplicate=(await s.execute(text('''SELECT 1 FROM club_players cp JOIN players p ON p.id=cp.player_id
        WHERE cp.club_id=:c AND COALESCE(p.template_id,p.id)=COALESCE((SELECT p2.template_id FROM players p2 WHERE p2.id=:p),:p) LIMIT 1'''),
        {'c':buyer,'p':offer['player_id']})).first()
    if duplicate: raise ValueError('У вас уже есть такой игрок.')
    await record_financial_transaction(s,buyer,'TRANSFER_OUT',-offer['amount'],f'Трансфер: {display_player_name(offer.get("first_name"),offer.get("last_name"))}')
    await record_financial_transaction(s,seller,'TRANSFER_IN',offer['amount'],f'Продажа: {display_player_name(offer.get("first_name"),offer.get("last_name"))}')
    await s.execute(text('DELETE FROM club_players WHERE club_id=:c AND player_id=:p'),{'c':seller,'p':offer['player_id']})
    await s.execute(text('INSERT INTO club_players(club_id,player_id,template_id,contract_salary,release_clause) SELECT :c,:p,template_id,:sal,:clause FROM players WHERE id=:p'),{'c':buyer,'p':offer['player_id'],'sal':max(15000,int(offer['amount'])//35),'clause':max(int(offer['amount'])*2,1000000)})
    await s.execute(text("UPDATE transfer_listings SET status='SOLD' WHERE id=:l"),{'l':offer['listing_id']})
    await s.execute(text("UPDATE transfer_offers SET status=CASE WHEN id=:o THEN 'ACCEPTED' ELSE 'REJECTED' END WHERE listing_id=:l AND status='PENDING'"),{'o':offer_id,'l':offer['listing_id']})
    await s.execute(text('INSERT INTO transfers(player_id,from_club_id,to_club_id,price) VALUES(:p,:f,:t,:a)'),{'p':offer['player_id'],'f':seller,'t':buyer,'a':offer['amount']})
    pname=display_player_name(offer.get('first_name'),offer.get('last_name'))
    await queue_club_notification(s,buyer,'TRANSFER','🤝 Трансфер завершён',f'{pname} перешёл в твой клуб за €{offer["amount"]:,}.',f'transfer-complete:{offer["id"]}:buyer')
    await queue_club_notification(s,seller,'TRANSFER','💰 Игрок продан',f'{pname} продан за €{offer["amount"]:,}.',f'transfer-complete:{offer["id"]}:seller')
    return offer

async def buy_player(s, club_id, player_id):
    player=(await s.execute(text('''SELECT p.id,p.first_name,p.last_name,p.market_value,p.age,p.potential,
        COALESCE(p.template_id,p.id) AS source_template_id FROM players p
        WHERE p.id=:p AND NOT EXISTS(SELECT 1 FROM club_players cp WHERE cp.player_id=p.id) FOR UPDATE'''),{'p':player_id})).mappings().first()
    if not player: raise ValueError('Player is no longer available on the market.')
    club=(await s.execute(text('SELECT budget FROM clubs WHERE id=:c FOR UPDATE'),{'c':club_id})).mappings().first()
    if not club: raise ValueError('Club not found.')
    duplicate=(await s.execute(text('''SELECT 1 FROM club_players cp JOIN players p ON p.id=cp.player_id
        WHERE cp.club_id=:c AND COALESCE(p.template_id,p.id)=:t LIMIT 1'''),
        {'c':club_id,'t':int(player['source_template_id'])})).first()
    if duplicate: raise ValueError('У вас уже есть такой игрок.')
    price=int(player['market_value'])
    if club['budget']<price: raise ValueError(f'Not enough budget. Need €{price:,}.')
    await record_financial_transaction(s,club_id,'TRANSFER_OUT',-price,f'Покупка {display_player_name(player.get("first_name"),player.get("last_name"))} за €{price:,}')
    await s.execute(text('INSERT INTO club_players(club_id,player_id,template_id,contract_salary,release_clause) SELECT :c,:p,template_id,:sal,:clause FROM players WHERE id=:p'),{'c':club_id,'p':player_id,'sal':max(15000,price//35),'clause':max(price*2,1000000)})
    await s.execute(text('INSERT INTO transfers(player_id,from_club_id,to_club_id,price) VALUES(:p,NULL,:c,:price)'),{'p':player_id,'c':club_id,'price':price})
    return player,price

async def sell_player(s, club_id, player_id):
    row=(await s.execute(text('''SELECT p.id,p.first_name,p.last_name,p.market_value,cp.shirt_number FROM club_players cp JOIN players p ON p.id=cp.player_id WHERE cp.club_id=:c AND cp.player_id=:p FOR UPDATE'''),{'c':club_id,'p':player_id})).mappings().first()
    if not row: raise ValueError('This player is not in your squad.')
    count=(await s.execute(text('SELECT count(*) FROM club_players WHERE club_id=:c'),{'c':club_id})).scalar_one()
    if count<=18: raise ValueError('You must keep at least 18 players in the squad.')
    price=max(50_000,int(row['market_value']*0.70))
    await s.execute(text("UPDATE transfer_listings SET status='CANCELLED' WHERE player_id=:p AND seller_club_id=:c AND status='LISTED'"),{'p':player_id,'c':club_id})
    await s.execute(text('DELETE FROM club_players WHERE club_id=:c AND player_id=:p'),{'c':club_id,'p':player_id})
    await record_financial_transaction(s,club_id,'TRANSFER_IN',price,f'Продажа {display_player_name(row.get("first_name"),row.get("last_name"))} за €{price:,}')
    await s.execute(text('INSERT INTO transfers(player_id,from_club_id,to_club_id,price) VALUES(:p,:c,NULL,:price)'),{'p':player_id,'c':club_id,'price':price})
    return row,price

async def renew_contract(s, club_id, player_id, years=3, role=None):
    row=(await s.execute(text('''SELECT p.*,cp.contract_salary,cp.morale,cp.playing_time_expectation FROM club_players cp JOIN players p ON p.id=cp.player_id WHERE cp.club_id=:c AND cp.player_id=:p FOR UPDATE'''),{'c':club_id,'p':player_id})).mappings().first()
    if not row: raise ValueError('Player is not in your squad.')
    role=role or row['playing_time_expectation']
    overall=sum(row[a] for a in ('pace','shooting','passing','dribbling','defending','physical','stamina','mental'))//8
    offer=contract_offer(row['market_value'],overall,row['age'],years,role)
    import datetime
    await s.execute(text('UPDATE club_players SET contract_until=:d,contract_salary=:sal,release_clause=:clause,playing_time_expectation=:role WHERE club_id=:c AND player_id=:p'),{'d':datetime.date.today()+datetime.timedelta(days=365*offer.years),'sal':offer.salary,'clause':offer.release_clause,'role':role,'c':club_id,'p':player_id})
    return offer

async def set_training_plan(s, club_id, focus, intensity):
    focus=str(focus).upper(); intensity=str(intensity).upper()
    if focus not in {'ATTACK','PLAYMAKING','DEFENSE','PHYSICAL','TECHNICAL','MENTAL','BALANCED','RECOVERY'}: raise ValueError('Unknown training focus.')
    if intensity not in {'LIGHT','NORMAL','HIGH'}: raise ValueError('Unknown training intensity.')
    await s.execute(text('''INSERT INTO club_training(club_id,focus,intensity) VALUES(:c,:f,:i)
        ON CONFLICT(club_id) DO UPDATE SET focus=EXCLUDED.focus,intensity=EXCLUDED.intensity,updated_at=now()'''),{'c':club_id,'f':focus,'i':intensity})
    await s.execute(text('UPDATE club_players SET training_focus=:f,training_intensity=:i WHERE club_id=:c'),{'c':club_id,'f':focus,'i':intensity})
    return focus,intensity

async def train_squad(s, club_id):
    plan=(await s.execute(text("SELECT focus,intensity FROM club_training WHERE club_id=:c"),{'c':club_id})).mappings().first() or {'focus':'BALANCED','intensity':'NORMAL'}
    last_training_at=(await s.execute(text("SELECT max(last_training_at) FROM club_players WHERE club_id=:c"),{'c':club_id})).scalar_one()
    if last_training_at is not None:
        recent=(await s.execute(text("SELECT now() - CAST(:t AS timestamptz) < interval '20 hours'"), {'t': last_training_at})).scalar_one()
        if recent:
            remaining=(await s.execute(text("SELECT GREATEST(0, EXTRACT(EPOCH FROM (interval '20 hours' - (now() - CAST(:t AS timestamptz)))))"), {'t': last_training_at})).scalar_one()
            return [('COOLDOWN', int(remaining or 0), False)], None
    rows=(await s.execute(text('''SELECT cp.*,p.* FROM club_players cp JOIN players p ON p.id=cp.player_id WHERE cp.club_id=:c FOR UPDATE'''),{'c':club_id})).mappings().all()
    changes=[]
    # One batch id groups every per-player development row produced by this run.
    batch_id=token_urlsafe(18)
    for r in rows:
        overall=sum(r[a] for a in ('pace','shooting','passing','dribbling','defending','physical','stamina','mental'))/8
        tr=training_result(r['age'],r['potential'],overall,plan['focus'],plan['intensity'],r['minutes_played'],r['morale'],r['appearances'])
        updates={}
        for attr in ('pace','shooting','passing','dribbling','defending','physical','stamina','mental'):
            nv=attribute_growth(r[attr],r['potential'],tr.development_points,plan['focus'],attr)
            if nv!=r[attr]: updates[attr]=nv
        if updates:
            await s.execute(text('UPDATE players SET '+','.join(f'{a}=:{a}' for a in updates)+' WHERE id=:p'),{**updates,'p':r['player_id']})
        await s.execute(text('UPDATE club_players SET morale=GREATEST(0,LEAST(100,morale+:m)),development_points=development_points+:pts,last_training_at=now() WHERE club_id=:c AND player_id=:p'),{'m':tr.morale_delta,'pts':tr.development_points,'c':club_id,'p':r['player_id']})
        await s.execute(text('INSERT INTO player_development_log(club_id,player_id,focus,intensity,points,changes,batch_id) VALUES(:c,:p,:f,:i,:pts,:changes,:batch)'),{'c':club_id,'p':r['player_id'],'f':plan['focus'],'i':plan['intensity'],'pts':tr.development_points,'changes':json.dumps(updates),'batch':batch_id})
        changes.append((r['player_id'],tr.development_points,updates))
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
    if expectation not in {'STAR','KEY','SQUAD','PROSPECT'}: raise ValueError('Unknown playing-time expectation.')
    r=(await s.execute(text('UPDATE club_players SET playing_time_expectation=:e WHERE club_id=:c AND player_id=:p RETURNING player_id'),{'e':expectation,'c':club_id,'p':player_id})).first()
    if not r: raise ValueError('Player is not in your squad.')
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
    for ev in sorted(result.events,key=lambda e:(e.minute, e.type, e.player or '')):
        team=ev.team
        cid=match['home_club_id'] if team in {'HOME',match['home_name']} else match['away_club_id']
        pid=int(ev.player_id) if ev.player_id is not None else None
        sid=int(ev.secondary_player_id) if ev.secondary_player_id is not None else None
        event_rows.append((ev.minute,ev.type,cid,pid,sid,ev.text or '',ev.metadata or {}))
        await s.execute(text('''INSERT INTO match_events(match_id,minute,event_type,club_id,player_id,secondary_player_id,description,metadata)
            VALUES(:m,:min,:t,:c,:p,:sp,:d,:meta)'''),
            {'m':match['id'],'min':int(ev.minute),'t':ev.type,'c':cid,'p':pid,'sp':sid,'d':ev.text or '', 'meta':json.dumps(ev.metadata or {})})
    goals_by_player=defaultdict(int); assists_by_player=defaultdict(int)
    for _,typ,_,pid,_,_,_ in event_rows:
        if typ=='GOAL' and pid: goals_by_player[pid]+=1
        if typ=='ASSIST' and pid: assists_by_player[pid]+=1
    motm_id=next((p.id for p in home+away if p.name == result.man_of_the_match), None) if result.man_of_the_match else None
    for p in home+away:
        rating=float(result.ratings.get(p.name,6.2))
        await s.execute(text('''INSERT INTO match_player_stats(match_id,club_id,player_id,rating,minutes,goals,assists)
            VALUES(:m,:c,:p,:r,90,:g,:a)
            ON CONFLICT(match_id,player_id) DO UPDATE SET rating=EXCLUDED.rating,minutes=EXCLUDED.minutes,goals=EXCLUDED.goals,assists=EXCLUDED.assists'''),
            {'m':match['id'],'c':match['home_club_id'] if p in home else match['away_club_id'],'p':p.id,
             'r':rating,'g':goals_by_player.get(p.id,0),'a':assists_by_player.get(p.id,0)})
    await s.execute(text("""UPDATE matches SET home_score=:h,away_score=:a,home_xg=:hx,away_xg=:ax,
        home_possession=50,away_possession=50,current_minute=90,status='FINISHED',live_phase='FINISHED',
        halftime_locked=FALSE,started_at=COALESCE(started_at,now()),finished_at=now(),motm_player_id=:motm,
        home_halftime_score=NULL,away_halftime_score=NULL,halftime_data='{}'::jsonb,second_half_data='{}'::jsonb
        WHERE id=:m"""),
        {'h':int(result.home_score),'a':int(result.away_score),'hx':round(float(result.home_xg),2),'ax':round(float(result.away_xg),2),'motm':motm_id,'m':match['id']})
    return {'match_id':match['id'],'home':match['home_name'],'away':match['away_name'],
            'home_score':int(result.home_score),'away_score':int(result.away_score)}


async def archive_league(s, league_id):
    """Archive the final standings and release all league club slots."""
    league=(await s.execute(text("SELECT id,name,status FROM leagues WHERE id=:l FOR UPDATE"),{'l':league_id})).mappings().first()
    if not league:
        raise ValueError('Лига не найдена.')
    rows=(await s.execute(text("""SELECT t.club_id,c.name club_name,t.played,t.wins,t.draws,t.losses,
            t.goals_for,t.goals_against,t.points
        FROM league_teams t JOIN clubs c ON c.id=t.club_id
        WHERE t.league_id=:l
        ORDER BY t.points DESC,(t.goals_for-t.goals_against) DESC,t.goals_for DESC,c.id"""),{'l':league_id})).mappings().all()
    if not rows:
        await s.execute(text("UPDATE leagues SET status='FINISHED',finished_at=now() WHERE id=:l"),{'l':league_id})
        return 0
    season_number=int((await s.execute(text('SELECT COALESCE(MAX(season_number),0)+1 FROM league_history WHERE league_id=:l'),{'l':league_id})).scalar_one())
    for place,row in enumerate(rows,1):
        await s.execute(text("""INSERT INTO league_history
            (league_id,league_name,season_number,club_id,club_name,place,played,wins,draws,losses,goals_for,goals_against,points)
            VALUES(:l,:ln,:sn,:c,:cn,:place,:played,:wins,:draws,:losses,:gf,:ga,:points)"""), {
            'l':league_id,'ln':league['name'],'sn':season_number,'c':row['club_id'],'cn':row['club_name'],
            'place':place,'played':row['played'],'wins':row['wins'],'draws':row['draws'],'losses':row['losses'],
            'gf':row['goals_for'],'ga':row['goals_against'],'points':row['points'],
        })
    await s.execute(text('DELETE FROM league_teams WHERE league_id=:l'),{'l':league_id})
    await s.execute(text("UPDATE leagues SET status='FINISHED',finished_at=now() WHERE id=:l"),{'l':league_id})
    return season_number


async def fast_play_round(s, league_id):
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
    await finalize_live_round(s,league_id,results)
    return round_no,results


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
    if not finished: return False
    league=(await s.execute(text('SELECT id,current_round,total_rounds,status FROM leagues WHERE id=:l FOR UPDATE'),{'l':league_id})).mappings().first()
    if not league or league['status']!='ACTIVE': return False
    for r in finished:
        m=(await s.execute(text('SELECT round,home_club_id,away_club_id FROM matches WHERE id=:m AND league_id=:l'),{'m':r['match_id'],'l':league_id})).mappings().first()
        if not m: continue
        h,a=r['home_score'],r['away_score']
        if h>a: pts=((m['home_club_id'],1,0,0,3),(m['away_club_id'],0,0,1,0))
        elif h<a: pts=((m['home_club_id'],0,0,1,0),(m['away_club_id'],1,0,0,3))
        else: pts=((m['home_club_id'],0,1,0,1),(m['away_club_id'],0,1,0,1))
        for cid,w,d,l,p in pts:
            gf=h if cid==m['home_club_id'] else a; ga=a if cid==m['home_club_id'] else h
            await s.execute(text("""UPDATE league_teams SET played=played+1,wins=wins+:w,draws=draws+:d,losses=losses+:ld,
                goals_for=goals_for+:gf,goals_against=goals_against+:ga,points=points+:p WHERE league_id=:l AND club_id=:c"""),{'w':w,'d':d,'ld':l,'gf':gf,'ga':ga,'p':p,'l':league_id,'c':cid})
        await settle_match_economy(s,league_id,r['match_id'],m['home_club_id'],m['away_club_id'])
    pending=(await s.execute(text("SELECT count(*) FROM matches WHERE league_id=:l AND round=:r AND status!='FINISHED'"),{'l':league_id,'r':league['current_round']})).scalar_one()
    if pending==0:
        final=league['current_round']>=league['total_rounds']
        if final:
            prizes=await settle_season_prizes(s,league_id)
            for cid,position,amount in prizes:
                await queue_club_notification(s,cid,'TOURNAMENT','🏆 Сезон завершён',
                    f'Вы заняли {position}-е место. Призовые: €{amount:,}.',
                    f'season-finished:{league_id}:{cid}')
            await archive_league(s,league_id)
        else:
            await s.execute(text("UPDATE leagues SET current_round=:n,status='ACTIVE' WHERE id=:l"),{'n':league['current_round']+1,'l':league_id})
        return True
    return False



async def deliver_due_notifications(bot, s, limit=50):
    """Claim due notifications, send them concurrently, then persist results."""
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
                    markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='📺 Смотреть матч',callback_data=f'match:watch:{mid}')]])
                elif key.startswith('match-finished:'):
                    mid=key.split(':',1)[1]
                    markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='📊 Полный отчёт',callback_data=f'match:watch:report:{mid}')]])
            if len(rows_for_send)==1:
                text_value=f"<b>{row['title']}</b>\n{str(row['body'] or '').replace(r'\n','\n')}"
            else:
                lines=['<b>📋 Уведомления:</b>']
                for item in rows_for_send:
                    body=str(item['body'] or '').replace(r'\n','\n').replace('\n',' ')
                    lines.append(f"• {item['title']}: {body}")
                text_value='\n'.join(lines)
            await bot.send_message(row['telegram_id'],text_value,reply_markup=markup)
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
