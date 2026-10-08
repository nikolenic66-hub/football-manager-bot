from secrets import token_urlsafe
import json
from datetime import datetime, timezone, timedelta
from sqlalchemy import text
from app.game.engine import simulate_match, expected_goals
from app.game.tactics import Tactics, Style
from app.game.league import round_robin
from app.game.board import FORMATION_SLOTS
from app.models import Player
from app.game.economy import calculate_match_economy, sponsor_income, wage_bill, prize_money, stadium_capacity, TICKET_MIN, TICKET_MAX

PLAYER_FIELDS='id,first_name,last_name,position,age,pace,shooting,passing,dribbling,defending,physical,stamina,mental,talents'


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
    n=(await s.execute(text('SELECT count(*) FROM club_players WHERE club_id=:c'),{'c':club_id})).scalar_one()
    if n>=23: return n
    need=23-n
    rows=(await s.execute(text(f'''SELECT {PLAYER_FIELDS} FROM players p
        WHERE NOT EXISTS(SELECT 1 FROM club_players cp WHERE cp.player_id=p.id)
        ORDER BY p.potential DESC,p.id LIMIT :n'''),{'n':need})).mappings().all()
    if len(rows)<need: raise RuntimeError('Not enough free players in the market.')
    for r in rows:
        await s.execute(text('INSERT INTO club_players(club_id,player_id) VALUES(:c,:p)'),{'c':club_id,'p':r['id']})
    await s.execute(text("""UPDATE club_players cp SET contract_salary=COALESCE(cp.contract_salary,p.salary),release_clause=CASE WHEN cp.release_clause=0 THEN GREATEST(p.market_value*2,p.salary*36) ELSE cp.release_clause END,contract_until=COALESCE(cp.contract_until,CURRENT_DATE+INTERVAL '3 years') FROM players p WHERE cp.club_id=:c AND p.id=cp.player_id"""),{'c':club_id})
    await s.execute(text('INSERT INTO club_training(club_id) VALUES(:c) ON CONFLICT DO NOTHING'),{'c':club_id})
    return n+need

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
    # leaving a buffer after the ~4-minute live match.
    season_start=datetime.now(timezone.utc)+timedelta(minutes=10)
    for f in fixtures:
        seed=f.round_no*1_000_003+f.home_club_id*1009+f.away_club_id
        kickoff=season_start+timedelta(minutes=(f.round_no-1)*10)
        await s.execute(text('''INSERT INTO matches(league_id,round,home_club_id,away_club_id,seed,started_at,scheduled_at,prestart_notified_at)
            VALUES(:l,:r,:h,:a,:seed,NULL,:kickoff,NULL)'''),
            {'l':league['id'],'r':f.round_no,'h':f.home_club_id,'a':f.away_club_id,'seed':seed,'kickoff':kickoff})
        for cid in (f.home_club_id, f.away_club_id):
            await queue_club_notification(s,cid,'MATCH','⏰ Ваш матч начнётся через 10 минут',
                'Подготовьтесь к матчу. Отдельного уведомления о стартовом свистке не будет.',
                f'match-start-10:{league["id"]}:{f.round_no}:{cid}',kickoff-timedelta(minutes=10))
    total=max(f.round_no for f in fixtures)
    await s.execute(text("UPDATE leagues SET status='ACTIVE',current_round=1,total_rounds=:r,started_at=:started WHERE id=:l"),
                     {'r':total,'started':season_start,'l':league['id']})
    return league['id'],len(fixtures),total

async def _starting_players(s,club_id):
    rows=(await s.execute(text(f"SELECT {PLAYER_FIELDS},cp.fitness,cp.form,cp.morale,cp.is_injured,cp.injury_matches,cp.suspension_matches FROM club_players cp JOIN players p ON p.id=cp.player_id WHERE cp.club_id=:c AND cp.is_injured=false AND cp.suspension_matches=0 ORDER BY p.potential DESC,p.id"),{'c':club_id})).mappings().all()
    by_id={r['id']:row_player(r) for r in rows}
    tactics=await _club_tactics(s,club_id)
    slots=[slot for slot,_,_ in FORMATION_SLOTS[tactics.formation]]
    saved=(await s.execute(text('SELECT player_ids FROM club_lineups WHERE club_id=:c'),{'c':club_id})).scalar_one_or_none()
    if saved:
        chosen=[]
        for pid in saved:
            if pid in by_id and pid not in {p.id for p in chosen}: chosen.append(by_id[pid])
            if len(chosen)==11: break
        if len(chosen)==11:
            return chosen
    by={}
    for r in rows: by.setdefault(r['position'],[]).append(by_id[r['id']])
    groups={
        'GK':['GK'], 'LB':['LB','RB','LW'], 'RB':['RB','LB','RW'], 'CB':['CB','LB','RB'],
        'DM':['DM','CM','CB'], 'CM':['CM','DM','AM'], 'AM':['AM','CM','ST'],
        'LM':['LW','RW','CM','AM'], 'RM':['RW','LW','CM','AM'],
        'LW':['LW','RW','AM','ST'], 'RW':['RW','LW','AM','ST'], 'ST':['ST','AM','RW','LW'],
    }
    chosen=[]; used=set()
    for role in slots:
        choices=[p for q in groups.get(role,[role]) for p in by.get(q,[]) if p.id not in used]
        if not choices: raise ValueError(f'Club {club_id} lacks a player for {role}.')
        p=choices[0]; chosen.append(p); used.add(p.id)
    return chosen

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


async def _club_board(s, club_id, players, formation):
    from app.game.board import TacticalBoard
    rows=(await s.execute(text("SELECT player_id,role,instruction,board_x,board_y FROM club_player_instructions WHERE club_id=:c"),{"c":club_id})).mappings().all()
    by={r["player_id"]:r for r in rows}
    board=TacticalBoard(formation)
    for i,p in enumerate(players):
        r=by.get(p.id)
        if r:
            board=board.assign(i,r["role"],r["instruction"]).reposition(i,r["board_x"],r["board_y"])
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
    return (await s.execute(text("""SELECT p.id,p.first_name,p.last_name,p.position,cp.shirt_number,
        COALESCE(i.role,'STARTER') role,COALESCE(i.instruction,'BALANCED') instruction,
i.board_x board_x,i.board_y board_y
        FROM club_players cp JOIN players p ON p.id=cp.player_id
        LEFT JOIN club_player_instructions i ON i.club_id=cp.club_id AND i.player_id=cp.player_id
        WHERE cp.club_id=:c ORDER BY cp.shirt_number NULLS LAST,p.id"""),{"c":club_id})).mappings().all()


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
        events={f"{r['first_name']} {r['last_name']}":r['n'] for r in ev}
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
    player=(await s.execute(text('''SELECT p.id,p.first_name,p.last_name,p.market_value FROM players p
        WHERE p.id=:p AND NOT EXISTS(SELECT 1 FROM club_players cp WHERE cp.player_id=p.id) FOR UPDATE'''),{'p':player_id})).mappings().first()
    if not player: raise ValueError('Player is no longer available on the market.')
    club=(await s.execute(text('SELECT budget FROM clubs WHERE id=:c FOR UPDATE'),{'c':club_id})).mappings().first()
    if not club: raise ValueError('Club not found.')
    price=int(player['market_value'])
    if club['budget']<price: raise ValueError(f'Not enough budget. Need €{price:,}.')
    await record_financial_transaction(s,club_id,'TRANSFER_OUT',-price,f'Покупка {player["first_name"]} {player["last_name"]} за €{price:,}')
    await s.execute(text('INSERT INTO club_players(club_id,player_id) VALUES(:c,:p)'),{'c':club_id,'p':player_id})
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
    await record_financial_transaction(s,club_id,'TRANSFER_IN',price,f'Продажа {row["first_name"]} {row["last_name"]} за €{price:,}')
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
        await s.execute(text("UPDATE matches SET scheduled_at=:at WHERE league_id=:l AND round=:r AND status='SCHEDULED'"),{'at':scheduled_at,'l':league_id,'r':round_no})
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
        await s.execute(text("UPDATE matches SET home_score=0,away_score=0,home_xg=:hx,away_xg=:ax,home_halftime_score=:hs,away_halftime_score=:as_,current_minute=0,status='LIVE',halftime_locked=FALSE,halftime_data=:data,second_half_data='{}'::jsonb,live_phase='FIRST_HALF',phase_started_at=:phase_start,halftime_ends_at=NULL,second_half_started_at=NULL,live_event_cursor=0,started_at=COALESCE(started_at,:phase_start),finished_at=NULL WHERE id=:m"),{'hs':hs,'as_':as_,'hx':round((expected_goals(home,away,ht,at)[0]*0.48),2),'ax':round((expected_goals(home,away,ht,at)[1]*0.48),2),'m':m['id'],'phase_start':phase_start,'data':json.dumps({'home_tactics':_tactics_json(ht),'away_tactics':_tactics_json(at),'events':[e.__dict__ for e in events], 'home_board':[(z.x,z.y,z.role,z.instruction) for z in hb.slots], 'away_board':[(z.x,z.y,z.role,z.instruction) for z in ab.slots]})})
        await _save_lineup_and_tactics(s,m['id'],m['home_club_id'],home,ht); await _save_lineup_and_tactics(s,m['id'],m['away_club_id'],away,at)
        home_ids={p.name:p.id for p in home}; away_ids={p.name:p.id for p in away}
        for e in events:
            pool=home_ids if e.team=='HOME' else away_ids; cid=m['home_club_id'] if e.team=='HOME' else m['away_club_id']
            await s.execute(text('INSERT INTO match_events(match_id,minute,event_type,club_id,player_id,secondary_player_id,description,metadata) VALUES(:m,:min,:t,:c,:p,:sp,:d,:meta)'),{'m':m['id'],'min':e.minute,'t':e.type,'c':cid,'p':pool.get(e.player),'sp':pool.get(e.secondary_player),'d':e.text,'meta':json.dumps(e.metadata)})
        out.append({'match_id':m['id'],'home':m['home_club_id'],'away':m['away_club_id'],'home_score':hs,'away_score':as_,'events':events})
    return out

async def change_halftime_tactics(s, match_id, club_id, tactics):
    match=(await s.execute(text("SELECT id,status,home_club_id,away_club_id,home_second_tactics,away_second_tactics FROM matches WHERE id=:m FOR UPDATE"),{'m':match_id})).mappings().first()
    if not match or match['status']!='LIVE' or not match['halftime_locked']: raise ValueError('Match is not at halftime.')
    if club_id not in (match['home_club_id'],match['away_club_id']): raise ValueError('You are not playing this match.')
    col='home_second_tactics' if club_id==match['home_club_id'] else 'away_second_tactics'
    await s.execute(text(f"UPDATE matches SET {col}=:t WHERE id=:m"),{'t':json.dumps(_tactics_json(tactics)),'m':match_id})
    await s.execute(text('UPDATE club_tactics SET formation=:f,style=:s,tempo=:t,pressing=:p,width=:w,defensive_line=:d,aggression=:a WHERE club_id=:c'),{'f':tactics.formation,'s':tactics.style.value,'t':tactics.tempo,'p':tactics.pressing,'w':tactics.width,'d':tactics.defensive_line,'a':tactics.aggression,'c':club_id})




# --- Real-time live match lifecycle ---
LIVE_SECONDS_PER_MINUTE = 2
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
    home_all=home + await _bench_players(s,match['home_club_id'],[x.id for x in home]); away_all=away + await _bench_players(s,match['away_club_id'],[x.id for x in away])
    home_by_name={p.name:p for p in home_all}; away_by_name={p.name:p for p in away_all}
    for e in events:
        pool=home_by_name if e.team=='HOME' else away_by_name; cid=match['home_club_id'] if e.team=='HOME' else match['away_club_id']
        pid=pool.get(e.player).id if e.player in pool else None; sp=pool.get(e.secondary_player).id if e.secondary_player in pool else None
        await s.execute(text('INSERT INTO match_events(match_id,minute,event_type,club_id,player_id,secondary_player_id,description,metadata) VALUES(:m,:min,:t,:c,:p,:sp,:d,:meta)'),{'m':match['id'],'min':e.minute,'t':e.type,'c':cid,'p':pid,'sp':sp,'d':e.text,'meta':json.dumps(e.metadata)})
    final_h=(match['home_halftime_score'] or 0)+hs; final_a=(match['away_halftime_score'] or 0)+as_
    data={'home':match['home_club_id'],'away':match['away_club_id'],'home_name':hname,'away_name':aname,'final_h':final_h,'final_a':final_a,
          'home_players':[p.id for p in original_home],'away_players':[p.id for p in original_away],
          'first_half_exits':first_half_exits,
          'substitutions':[(int(a),int(b),int(c.id),d) for a,b,c,d in substitutions],
          'events':[e.__dict__ for e in events],
          'home_tactics':_tactics_json(ht),'away_tactics':_tactics_json(at)}
    hx,ax=expected_goals(home,away,ht,at)
    phase_start_at = phase_start_at or datetime.now(timezone.utc)
    await s.execute(text("""UPDATE matches SET current_minute=45,live_phase='SECOND_HALF',halftime_locked=FALSE,
        second_half_started_at=:start_at,phase_started_at=:start_at,halftime_ends_at=NULL,second_half_data=:data,
        home_xg=ROUND((COALESCE(home_xg,0)+:hx*0.52)::numeric,2),
        away_xg=ROUND((COALESCE(away_xg,0)+:ax*0.52)::numeric,2) WHERE id=:m"""),
        {'m':match['id'],'hs':int(match['home_halftime_score'] or 0),'as_':int(match['away_halftime_score'] or 0),'hx':hx,'ax':ax,'data':json.dumps(data),'start_at':phase_start_at})
    return data

async def _finish_live_match(s, match):
    data=match['second_half_data'] or {}
    if not data: raise ValueError('Second-half state is missing.')
    final_h=int(data['final_h']); final_a=int(data['final_a'])
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
                    f"{rr['first_name']} {rr['last_name']} полностью восстановился и снова доступен.",
                    f'injury-recovered:{cid}:{rr["player_id"]}:{match["id"]}')
            if int(rr['suspension_matches'] or 0)==1:
                await queue_club_notification(s,cid,'DISCIPLINE','✅ Дисквалификация закончилась',
                    f"{rr['first_name']} {rr['last_name']} снова доступен после дисквалификации.",
                    f'suspension-ended:{cid}:{rr["player_id"]}:{match["id"]}')
    discipline=(await s.execute(text("SELECT club_id,player_id,event_type FROM match_events WHERE match_id=:m AND event_type IN ('INJURY','RED') AND player_id IS NOT NULL"),{'m':match['id']})).mappings().all()
    for e in discipline:
        pname=(await s.execute(text("SELECT first_name||' '||last_name FROM players WHERE id=:p"),{'p':e['player_id']})).scalar_one_or_none() or 'Игрок'
        if e['event_type']=='INJURY':
            await s.execute(text('UPDATE club_players SET is_injured=true,injury_matches=GREATEST(injury_matches,2) WHERE club_id=:c AND player_id=:p'),{'c':e['club_id'],'p':e['player_id']})
            await queue_club_notification(s,e['club_id'],'INJURY','🩹 Игрок получил травму',f'{pname} получил травму. Ожидаемое восстановление — минимум 2 матча.',f'injury:{match["id"]}:{e["player_id"]}')
        elif e['event_type']=='RED':
            await s.execute(text('UPDATE club_players SET suspension_matches=GREATEST(suspension_matches,1) WHERE club_id=:c AND player_id=:p'),{'c':e['club_id'],'p':e['player_id']})
            await queue_club_notification(s,e['club_id'],'DISCIPLINE','🟥 Удаление',f'{pname} получил красную карточку и пропустит следующий матч.',f'red:{match["id"]}:{e["player_id"]}')
    # Persist live player ratings/MOTM inputs before the match becomes FINISHED.
    events=(await s.execute(text('SELECT minute,player_id,secondary_player_id,event_type FROM match_events WHERE match_id=:m ORDER BY id'), {'m':match['id']})).mappings().all()
    home_ids=set(data.get('home_players',[])); away_ids=set(data.get('away_players',[]))
    for minute,off_id,on_id,team in data.get('substitutions',[]):
        (home_ids if team=='HOME' else away_ids).add(int(on_id))
    player_ids=home_ids | away_ids
    motm_player_id=None
    motm_rating=-1.0
    if player_ids:
        prs=(await s.execute(text(f"SELECT {PLAYER_FIELDS} FROM players WHERE id=ANY(:ids)"), {'ids':list(player_ids)})).mappings().all()
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
    result_text=f"{data.get('home_name','Home')} <b>{final_h}:{final_a}</b> {data.get('away_name','Away')}"
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
        if phase=='FIRST_HALF':
            minute=min(45,int(elapsed//LIVE_SECONDS_PER_MINUTE))
            goals=(await s.execute(text("SELECT club_id,count(*) n FROM match_events WHERE match_id=:m AND event_type='GOAL' AND minute<=:minute GROUP BY club_id"),{'m':match['id'],'minute':minute})).mappings().all()
            hg=next((int(x['n']) for x in goals if x['club_id']==match['home_club_id']),0)
            ag=next((int(x['n']) for x in goals if x['club_id']==match['away_club_id']),0)
            await s.execute(text('UPDATE matches SET current_minute=:min,home_score=:hs,away_score=:as_ WHERE id=:m'),{'min':minute,'hs':hg,'as_':ag,'m':match['id']})
            if elapsed>=LIVE_FIRST_HALF_SECONDS:
                first_half_end=phase_start+timedelta(seconds=LIVE_FIRST_HALF_SECONDS)
                end=first_half_end+timedelta(seconds=LIVE_HALFTIME_SECONDS)
                await s.execute(text("UPDATE matches SET current_minute=45,live_phase='HALFTIME',halftime_locked=TRUE,halftime_ends_at=:end,phase_started_at=:phase WHERE id=:m"),{'end':end,'phase':first_half_end,'m':match['id']})
                transitions.append((match['id'],'HALFTIME'))
        elif phase=='HALFTIME':
            end=match.get('halftime_ends_at')
            if end is not None and now>=end:
                fresh=(await s.execute(text('SELECT * FROM matches WHERE id=:m FOR UPDATE'),{'m':match['id']})).mappings().first()
                if fresh and fresh['live_phase']=='HALFTIME':
                    second_start=fresh['halftime_ends_at']
                    await _prepare_second_half_live(s,fresh,second_start)
                    transitions.append((match['id'],'SECOND_HALF'))
                    if now >= second_start+timedelta(seconds=LIVE_SECOND_HALF_SECONDS):
                        refreshed=(await s.execute(text('SELECT * FROM matches WHERE id=:m FOR UPDATE'),{'m':match['id']})).mappings().first()
                        if refreshed and refreshed['live_phase']=='SECOND_HALF':
                            result=await _finish_live_match(s,refreshed)
                            finished.append(result); transitions.append((match['id'],'FULL_TIME'))
        elif phase=='SECOND_HALF':
            minute=min(90,45+int(elapsed//LIVE_SECONDS_PER_MINUTE))
            goals=(await s.execute(text('''SELECT club_id,count(*) n FROM match_events WHERE match_id=:m AND event_type='GOAL' AND minute<=:minute GROUP BY club_id'''),{'m':match['id'],'minute':minute})).mappings().all()
            hg=next((int(x['n']) for x in goals if x['club_id']==match['home_club_id']),0)
            ag=next((int(x['n']) for x in goals if x['club_id']==match['away_club_id']),0)
            await s.execute(text('UPDATE matches SET current_minute=:min,home_score=:hs,away_score=:as_ WHERE id=:m'),{'min':minute,'hs':int(match['home_halftime_score'] or 0)+hg,'as_':int(match['away_halftime_score'] or 0)+ag,'m':match['id']})
            if elapsed>=LIVE_SECOND_HALF_SECONDS:
                fresh=(await s.execute(text('SELECT * FROM matches WHERE id=:m FOR UPDATE'),{'m':match['id']})).mappings().first()
                if fresh and fresh['live_phase']=='SECOND_HALF':
                    result=await _finish_live_match(s,fresh)
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
    await queue_club_notification(s,listing['seller_club_id'],'TRANSFER','📩 Новое трансферное предложение',f"Предложение €{amount:,} за {player['first_name']} {player['last_name']}. Решение ожидает тебя.",f'transfer-offer:{oid}')
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
    await record_financial_transaction(s,buyer,'TRANSFER_OUT',-offer['amount'],f'Трансфер: {offer["first_name"]} {offer["last_name"]}')
    await record_financial_transaction(s,seller,'TRANSFER_IN',offer['amount'],f'Продажа: {offer["first_name"]} {offer["last_name"]}')
    await s.execute(text('DELETE FROM club_players WHERE club_id=:c AND player_id=:p'),{'c':seller,'p':offer['player_id']})
    await s.execute(text('INSERT INTO club_players(club_id,player_id,contract_salary,release_clause) VALUES(:c,:p,:sal,:clause)'),{'c':buyer,'p':offer['player_id'],'sal':max(15000,int(offer['amount'])//35),'clause':max(int(offer['amount'])*2,1000000)})
    await s.execute(text("UPDATE transfer_listings SET status='SOLD' WHERE id=:l"),{'l':offer['listing_id']})
    await s.execute(text("UPDATE transfer_offers SET status=CASE WHEN id=:o THEN 'ACCEPTED' ELSE 'REJECTED' END WHERE listing_id=:l AND status='PENDING'"),{'o':offer_id,'l':offer['listing_id']})
    await s.execute(text('INSERT INTO transfers(player_id,from_club_id,to_club_id,price) VALUES(:p,:f,:t,:a)'),{'p':offer['player_id'],'f':seller,'t':buyer,'a':offer['amount']})
    pname=f"{offer['first_name']} {offer['last_name']}"
    await queue_club_notification(s,buyer,'TRANSFER','🤝 Трансфер завершён',f'{pname} перешёл в твой клуб за €{offer["amount"]:,}.',f'transfer-complete:{offer["id"]}:buyer')
    await queue_club_notification(s,seller,'TRANSFER','💰 Игрок продан',f'{pname} продан за €{offer["amount"]:,}.',f'transfer-complete:{offer["id"]}:seller')
    return offer

async def buy_player(s, club_id, player_id):
    player=(await s.execute(text('''SELECT p.id,p.first_name,p.last_name,p.market_value,p.age,p.potential FROM players p
        WHERE p.id=:p AND NOT EXISTS(SELECT 1 FROM club_players cp WHERE cp.player_id=p.id) FOR UPDATE'''),{'p':player_id})).mappings().first()
    if not player: raise ValueError('Player is no longer available on the market.')
    club=(await s.execute(text('SELECT budget FROM clubs WHERE id=:c FOR UPDATE'),{'c':club_id})).mappings().first()
    if not club: raise ValueError('Club not found.')
    price=int(player['market_value'])
    if club['budget']<price: raise ValueError(f'Not enough budget. Need €{price:,}.')
    await record_financial_transaction(s,club_id,'TRANSFER_OUT',-price,f'Покупка {player["first_name"]} {player["last_name"]} за €{price:,}')
    await s.execute(text('INSERT INTO club_players(club_id,player_id,contract_salary,release_clause) VALUES(:c,:p,:sal,:clause)'),{'c':club_id,'p':player_id,'sal':max(15000,price//35),'clause':max(price*2,1000000)})
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
    await record_financial_transaction(s,club_id,'TRANSFER_IN',price,f'Продажа {row["first_name"]} {row["last_name"]} за €{price:,}')
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
    recent=(await s.execute(text("SELECT now() - :t < interval '20 hours'"), {'t': last_training_at})).scalar_one()
    if recent:
        return []
    rows=(await s.execute(text('''SELECT cp.*,p.* FROM club_players cp JOIN players p ON p.id=cp.player_id WHERE cp.club_id=:c FOR UPDATE'''),{'c':club_id})).mappings().all()
    changes=[]
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
        await s.execute(text('INSERT INTO player_development_log(club_id,player_id,focus,points,changes) VALUES(:c,:p,:f,:pts,:changes)'),{'c':club_id,'p':r['player_id'],'f':plan['focus'],'pts':tr.development_points,'changes':json.dumps(updates)})
        changes.append((r['player_id'],tr.development_points,updates))
        if updates:
            labels=', '.join(f'{a} +{b-r[a]}' for a,b in updates.items())
            await queue_club_notification(s,club_id,'DEVELOPMENT','📈 Игрок вырос',f"{r['first_name']} {r['last_name']}: {labels}.",f'training-growth:{club_id}:{r["player_id"]}:{datetime.now(timezone.utc).date()}')
    return changes

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
        await s.execute(text("UPDATE leagues SET current_round=:n,status=:st,finished_at=CASE WHEN :f THEN now() ELSE finished_at END WHERE id=:l"),{'n':league['current_round'] if final else league['current_round']+1,'st':'FINISHED' if final else 'ACTIVE','f':final,'l':league_id})
        return True
    return False



async def deliver_due_notifications(bot, s, limit=50):
    """Claim due notifications without row-locking the nullable side of an outer join."""
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

    rows = (await s.execute(text("""SELECT n.id,n.user_id,n.category,n.title,n.body,u.telegram_id
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

    delivered=0
    for row in rows:
        try:
            await bot.send_message(row['telegram_id'], f"<b>{row['title']}</b>\n{row['body']}")
        except Exception as exc:
            attempts=(await s.execute(text('SELECT delivery_attempts FROM notifications WHERE id=:id'), {'id':row['id']})).scalar_one_or_none() or 1
            delay=min(300,2 ** min(int(attempts),8))
            if int(attempts) >= 8:
                await s.execute(text("""UPDATE notifications
                    SET processing_at=NULL,failed_at=now(),next_attempt_at=NULL,last_error=:err
                    WHERE id=:id AND sent_at IS NULL AND cancelled_at IS NULL"""), {'id':row['id'],'err':str(exc)[:500]})
            else:
                await s.execute(text("""UPDATE notifications
                    SET processing_at=NULL,next_attempt_at=now()+make_interval(secs=>:delay),last_error=:err
                    WHERE id=:id AND sent_at IS NULL AND cancelled_at IS NULL AND failed_at IS NULL"""), {'id':row['id'],'delay':delay,'err':str(exc)[:500]})
            await s.commit()
            continue
        await s.execute(text("""UPDATE notifications
            SET sent_at=now(),processing_at=NULL,last_error=NULL
            WHERE id=:id AND sent_at IS NULL"""), {'id':row['id']})
        await s.commit()
        delivered+=1
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
        VALUES(:u,:c,:t,:b,:d,COALESCE(:at,now()))
        ON CONFLICT(user_id,dedupe_key) WHERE dedupe_key IS NOT NULL DO NOTHING
        RETURNING id'''), {'u':user_id,'c':category,'t':title,'b':body,'d':dedupe_key,'at':deliver_at})).first()
    return row.id if row else None

async def queue_club_notification(s, club_id, category, title, body, dedupe_key=None, deliver_at=None):
    uid=(await s.execute(text('SELECT owner_user_id FROM clubs WHERE id=:c'), {'c':club_id})).scalar_one_or_none()
    if uid is None: return None
    return await queue_notification(s, uid, category, title, body, dedupe_key, deliver_at)

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
