from dataclasses import dataclass,field
from random import Random
from math import exp
from app.game.ratings import team_strength,player_rating
from app.game.tactics import Tactics,Style,tactical_modifiers
from app.game.talents import talent_bonus, player_talent_bonus
from app.game.board import TacticalBoard, board_effect, phase_profile
from app.game.match_state import fatigue_factor
@dataclass
class MatchEvent:
    minute:int; type:str; team:str; player:str|None=None; secondary_player:str|None=None; text:str=''; metadata:dict=field(default_factory=dict)
@dataclass
class MatchResult:
    home_score:int; away_score:int; home_xg:float; away_xg:float; events:list[MatchEvent]=field(default_factory=list); ratings:dict[str,float]=field(default_factory=dict); man_of_the_match:str|None=None


_EVENT_ORDER={'PASS':0,'CARRY':1,'PRESSURE':2,'TACKLE':3,'INTERCEPTION':3,'SHOT':4,'GOAL':5,'ASSIST':6,'FOUL':7,'YELLOW':8,'RED':9,'INJURY':10,'SUBSTITUTION':11}

def _player_map(players, board, team):
    slots=list(board.slots) if board is not None else list(TacticalBoard(players[0].position if False else '4-3-3').slots)
    by_id={}
    for i,p in enumerate(players):
        slot=slots[i] if i<len(slots) else slots[-1]
        # Engine depth x is mirrored for the away side so both teams attack toward x=100.
        x=100-slot.x if team=='AWAY' else slot.x
        by_id[p.id]=(float(x),float(slot.y))
    return by_id

def _choose_player(rng, players, exclude=None, weights=None):
    pool=[p for p in players if p.id != getattr(exclude,'id',None)]
    if not pool: return None
    if weights:
        return rng.choices(pool,weights=[max(.2,weights(p)) for p in pool],k=1)[0]
    return rng.choice(pool)

def _event_point(rng, pos, jitter=4):
    x,y=pos
    return [round(max(2,min(98,x+rng.uniform(-jitter,jitter))),1),round(max(4,min(96,y+rng.uniform(-jitter,jitter))),1)]

def _visual_meta(kind, start, end=None, result=None, pressure=0, zone=None):
    meta={'visual_type':kind,'start':[round(start[0],1),round(start[1],1)]}
    if end is not None: meta['end']=[round(end[0],1),round(end[1],1)]
    if result: meta['result']=result
    if pressure: meta['pressure']=round(float(pressure),1)
    if zone: meta['zone']=zone
    return meta

def _add_open_play_events(events, home, away, ht, at, home_board, away_board, seed, minute_start, minute_end, hx, ax, count=None):
    """Add deterministic tactical actions used by the visual replay. They do not alter the score/xG."""
    rng=Random(seed)
    hp=_player_map(home,home_board or TacticalBoard(ht.formation),'HOME'); ap=_player_map(away,away_board or TacticalBoard(at.formation),'AWAY')
    teams=[(home,'HOME',hp,max(.2,hx)),(away,'AWAY',ap,max(.2,ax))]
    total=count if count is not None else 6 + int((ht.tempo+at.tempo)/45) + int((ht.pressing+at.pressing)/80)
    total=max(1,total) if count is not None else max(8,min(14,total))
    for i in range(total):
        team,side,positions,strength=rng.choices(teams,weights=[t[3] for t in teams],k=1)[0]
        # Exclude goalkeepers from most open-play actions.
        pool=[p for p in team if p.position!='GK'] or list(team)
        actor=_choose_player(rng,pool,weights=lambda p: 1.4 if p.position in {'CM','AM','LW','RW','ST'} else 1.0)
        if actor is None: continue
        start=_event_point(rng,positions.get(actor.id,(50,50)),5)
        kind=rng.choices(['PASS','CARRY','PRESSURE','TACKLE','INTERCEPTION'],weights=[42,20,14,13,11],k=1)[0]
        minute=rng.randint(minute_start,minute_end)
        if kind=='PASS':
            receiver=_choose_player(rng,pool,actor,weights=lambda p: 1.5 if p.position in {'CM','AM','LW','RW','ST'} else 1.0)
            if not receiver: continue
            end=_event_point(rng,positions.get(receiver.id,start),4)
            events.append(MatchEvent(minute,'PASS',side,actor.name,receiver.name,f'↗️ {actor.name} → {receiver.name}',_visual_meta('PASS',start,end,zone='BUILD_UP' if start[0]<50 else 'ATTACK')))
        elif kind=='CARRY':
            direction=1 if side=='HOME' else -1
            end=[max(3,min(97,start[0]+direction*rng.uniform(6,15))),max(4,min(96,start[1]+rng.uniform(-9,9)))]
            events.append(MatchEvent(minute,'CARRY',side,actor.name,None,f'🏃 {actor.name} продвигает мяч',_visual_meta('CARRY',start,end,zone='TRANSITION')))
        else:
            opponent=away if side=='HOME' else home; opp_pos=ap if side=='HOME' else hp
            opp_pool=[p for p in opponent if p.position!='GK'] or list(opponent)
            target=_choose_player(rng,opp_pool,weights=lambda p: 1.4 if p.position in {'CM','AM','ST'} else 1.0)
            if not target: continue
            target_pt=_event_point(rng,opp_pos.get(target.id,(50,50)),4)
            if kind=='PRESSURE':
                events.append(MatchEvent(minute,'PRESSURE',side,actor.name,target.name,f'🔒 {actor.name} прессингует {target.name}',_visual_meta('PRESSURE',start,target_pt,pressure=max(ht.pressing,at.pressing),zone='PRESS')))
            elif kind=='TACKLE':
                events.append(MatchEvent(minute,'TACKLE',side,actor.name,target.name,f'🛡 {actor.name} отбирает у {target.name}',_visual_meta('TACKLE',start,target_pt,zone='DUEL')))
            else:
                events.append(MatchEvent(minute,'INTERCEPTION',side,actor.name,target.name,f'✂️ {actor.name} перехватывает передачу',_visual_meta('INTERCEPTION',start,target_pt,zone='DUEL')))

def _goal_sequence(events, scorer, assist, side, minute, positions, rng):
    scorer_pt=_event_point(rng,positions.get(scorer.id,(80,50)),3)
    if assist:
        assist_pt=_event_point(rng,positions.get(assist.id,(60,50)),3)
        events.append(MatchEvent(max(1,minute-1),'PASS',side,assist.name,scorer.name,f'↗️ {assist.name} → {scorer.name}',_visual_meta('PASS',assist_pt,scorer_pt,zone='CHANCE')))
    goal_y=max(18,min(82,50+rng.uniform(-20,20)))
    goal_x=100 if side=='HOME' else 0
    shot_end=[goal_x,round(goal_y,1)]
    events.append(MatchEvent(minute,'SHOT',side,scorer.name,None,f'🎯 {scorer.name} бьёт по воротам',_visual_meta('SHOT',scorer_pt,shot_end,'GOAL',zone='FINISH')))

def _add_non_goal_shots(events, home, away, ht, at, home_board, away_board, seed, minute_start, minute_end, count=2):
    rng=Random(seed); hp=_player_map(home,home_board or TacticalBoard(ht.formation),'HOME'); ap=_player_map(away,away_board or TacticalBoard(at.formation),'AWAY')
    for i in range(count):
        team,side,positions=(home,'HOME',hp) if rng.random()<.5 else (away,'AWAY',ap)
        pool=[p for p in team if p.position!='GK'] or list(team); shooter=_choose_player(rng,pool,weights=lambda p: 1.8 if p.position=='ST' else (1.35 if p.position in {'LW','RW','AM'} else 1.0))
        if not shooter: continue
        start=_event_point(rng,positions.get(shooter.id,(75,50)),3); end=[100 if side=='HOME' else 0,round(max(15,min(85,50+rng.uniform(-28,28))),1)]
        result=rng.choices(['SAVED','BLOCKED','WIDE'],weights=[48,32,20],k=1)[0]
        minute=rng.randint(minute_start,minute_end)
        events.append(MatchEvent(minute,'SHOT',side,shooter.name,None,f'🎯 {shooter.name} наносит удар',_visual_meta('SHOT',start,end,result,zone='FINISH')))

def poisson(rng,lam):
    if lam <= 0:
        return 0
    lam=min(lam,4.5); L=exp(-lam); k=0;p=1
    while p>L: k+=1;p*=rng.random()
    return k-1
def expected_goals(home,away,ht,at,home_board=None,away_board=None,home_counter=None,away_counter=None):
    if not home or not away:
        return 0.0, 0.0
    hs,as_=team_strength(home,ht.formation),team_strength(away,at.formation); ha,hd=tactical_modifiers(ht); aa,ad=tactical_modifiers(at)
    he=board_effect(home_board or TacticalBoard(ht.formation),ht.style,ht.pressing,ht.width,ht.defensive_line); ae=board_effect(away_board or TacticalBoard(at.formation),at.style,at.pressing,at.width,at.defensive_line)
    hb=home_board or TacticalBoard(ht.formation); ab=away_board or TacticalBoard(at.formation)
    he=board_effect(hb,ht.style,ht.pressing,ht.width,ht.defensive_line); ae=board_effect(ab,at.style,at.pressing,at.width,at.defensive_line)
    hp=phase_profile(hb,ht.style,ht.pressing,ht.width,ht.defensive_line); ap=phase_profile(ab,at.style,at.pressing,at.width,at.defensive_line)
    # Phase matchups: build-up under pressure, possession versus press, and transition space.
    h_press_res=sum(player_talent_bonus(p,'press_resistance') for p in home)
    a_press_res=sum(player_talent_bonus(p,'press_resistance') for p in away)
    h_build=sum(player_talent_bonus(p,'build_up') for p in home)
    a_build=sum(player_talent_bonus(p,'build_up') for p in away)
    hx_phase=hp['build_up']*(1+h_build*.18)*max(.90,1+(hp['possession']-ap['pressing'])*.45)*(1+h_press_res*.05)
    ax_phase=ap['build_up']*(1+a_build*.18)*max(.90,1+(ap['possession']-hp['pressing'])*.45)*(1+a_press_res*.05)
    h_transition=hp['transition_attack']*(1+talent_bonus(home,'transition')*.12+talent_bonus(home,'counter')*.08)
    a_transition=ap['transition_attack']*(1+talent_bonus(away,'transition')*.12+talent_bonus(away,'counter')*.08)
    if at.defensive_line>65: hx_phase*=h_transition
    if ht.defensive_line>65: ax_phase*=a_transition
    hx_phase*=hp['width']/max(.92,ap['width'])
    ax_phase*=ap['width']/max(.92,hp['width'])
    h_at=(.52*hs.attack+.30*hs.midfield+.18*hs.overall)*ha*he['central_control']*he['chance_creation']
    a_at=(.52*as_.attack+.30*as_.midfield+.18*as_.overall)*aa*ae['central_control']*ae['chance_creation']
    h_de=(.60*hs.defense+.40*hs.goalkeeper)*hd*he['transition_def']
    a_de=(.60*as_.defense+.40*as_.goalkeeper)*ad*ae['transition_def']
    hx=1.35*(h_at/max(1,a_de))**.90*1.06; ax=1.15*(a_at/max(1,h_de))**.90
    hx *= hx_phase * (1 + min(.12, talent_bonus(home, 'chance_creation'))); hx *= 1 + min(.05, (he['width_attack']-1)*.8)
    ax *= ax_phase * (1 + min(.12, talent_bonus(away, 'chance_creation'))); ax *= 1 + min(.05, (ae['width_attack']-1)*.8)
    if ht.style==Style.COUNTER and at.defensive_line>65: hx*=1.05 + min(.05, talent_bonus(home, 'counter'))
    if at.style==Style.COUNTER and ht.defensive_line>65: ax*=1.05 + min(.05, talent_bonus(away, 'counter'))
    if ht.pressing>70 and at.style==Style.POSSESSION: hx*=1.03 + min(.05, talent_bonus(home, 'pressing'))
    if at.pressing>70 and ht.style==Style.POSSESSION: ax*=1.03 + min(.05, talent_bonus(away, 'pressing'))
    # Explicit counter-tactics are small, explainable modifiers rather than hidden ratings.
    for plan, own, opp in ((home_counter, home, away), (away_counter, away, home)):
        if not plan: continue
        focus=str(plan.get('focus','BALANCED')).upper(); intensity=max(0,min(100,int(plan.get('intensity',50))))/100
        target_id=plan.get('target_player_id')
        target=next((p for p in opp if p.id==target_id),None) if target_id else None
        if focus=='PRESS_PLAYMAKER' and target and target.position in {'AM','CM','DM'}:
            if own is home: hx*=1+.035*intensity; ax*=1-.045*intensity
            else: ax*=1+.035*intensity; hx*=1-.045*intensity
        elif focus=='ATTACK_FLANKS' and ((opp is away and at.width < 45) or (opp is home and ht.width < 45)):
            if own is home: hx*=1+.035*intensity
            else: ax*=1+.035*intensity
        elif focus=='HIGH_LINE_TRAP' and opp is away and at.defensive_line>60:
            hx*=1+.055*intensity
        elif focus=='HIGH_LINE_TRAP' and opp is home and ht.defensive_line>60:
            ax*=1+.055*intensity
        elif focus=='TARGET_SLOW_CB' and target and target.position=='CB' and target.pace<70:
            if own is home: hx*=1+.045*intensity
            else: ax*=1+.045*intensity
        elif focus=='LOW_BLOCK' and ((opp is away and at.style in {Style.ATTACK,Style.POSSESSION}) or (opp is home and ht.style in {Style.ATTACK,Style.POSSESSION})):
            if own is home: ax*=1-.04*intensity
            else: hx*=1-.04*intensity
    return round(max(.15,min(hx,4)),2),round(max(.15,min(ax,4)),2)
def pick_scorer(rng,players):
    xs=[p for p in players if p.position!='GK' and not p.suspended]
    if not xs:
        return None
    return rng.choices(xs,weights=[max(1,player_rating(p))*(1.25 if p.position=='ST' else 1) for p in xs],k=1)[0]
def pick_assist(rng,players,scorer):
    xs=[p for p in players if p.id!=scorer.id and p.position!='GK' and not p.suspended]; return rng.choice(xs) if xs else None
def simulate_match(home_name,away_name,home,away,home_tactics,away_tactics,seed,home_board=None,away_board=None,home_counter=None,away_counter=None):
    if len(home)!=11 or len(away)!=11: raise ValueError('Each side must have 11 starting players.')
    rng=Random(seed); hx,ax=expected_goals(home,away,home_tactics,away_tactics,home_board,away_board,home_counter,away_counter); hs,as_=poisson(rng,hx),poisson(rng,ax); events=[]
    visual_rng=Random(seed*31+7)
    home_pos=_player_map(home,home_board or TacticalBoard(home_tactics.formation),'HOME')
    away_pos=_player_map(away,away_board or TacticalBoard(away_tactics.formation),'AWAY')
    for team,name,n,positions in ((home,home_name,hs,home_pos),(away,away_name,as_,away_pos)):
        for _ in range(n):
            p=pick_scorer(rng,team)
            if p is None: continue
            a=pick_assist(rng,team,p); minute=rng.randint(2,90)
            events.append(MatchEvent(minute,'GOAL',name,p.name,a.name if a else None,f'⚽ {p.name} забивает!'))
            _goal_sequence(events,p,a,'HOME' if team is home else 'AWAY',minute,positions,visual_rng)
            if a: events.append(MatchEvent(minute,'ASSIST',name,a.name,p.name,f'🅰️ Передача {a.name}.'))
    _add_open_play_events(events,home,away,home_tactics,away_tactics,home_board,away_board,seed*17+1,4,88,hx,ax)
    _add_non_goal_shots(events,home,away,home_tactics,away_tactics,home_board,away_board,seed*17+2,7,88,count=2+int((home_tactics.tempo+away_tactics.tempo)/100))
    for minute in sorted(rng.sample(range(8,89),k=rng.randint(1,4))):
        team=home if rng.random()<.5 else away;p=rng.choice(team)
        if p.position!='GK': events.append(MatchEvent(minute,'YELLOW',home_name if p in home else away_name,p.name,text=f'🟨 {p.name} получает жёлтую.'))
    events.sort(key=lambda e:(e.minute,_EVENT_ORDER.get(e.type,99))); ratings={}
    for p in home+away:
        r=6.2+(player_rating(p)-65)*.055+.15*(p.position=='GK')
        r += .85*sum(e.type=='GOAL' and e.player==p.name for e in events); r+=.45*sum(e.type=='ASSIST' and e.player==p.name for e in events); ratings[p.name]=round(max(5,min(10,r)),1)
    motm=max(ratings,key=ratings.get); return MatchResult(hs,as_,hx,ax,events,ratings,motm)


def _half_result(home, away, ht, at, seed, half, home_counter=None, away_counter=None, home_board=None, away_board=None):
    rng=Random(seed)
    hx,ax=expected_goals(home,away,ht,at,home_board=home_board,away_board=away_board,home_counter=home_counter,away_counter=away_counter)
    hx*=0.48; ax*=0.48
    hs,as_=poisson(rng,hx),poisson(rng,ax)
    events=[]
    visual_rng=Random(seed*31+half)
    home_pos=_player_map(home,home_board or TacticalBoard(ht.formation),'HOME')
    away_pos=_player_map(away,away_board or TacticalBoard(at.formation),'AWAY')
    for team,name,n,positions in ((home,'HOME',hs,home_pos),(away,'AWAY',as_,away_pos)):
        for _ in range(n):
            p=pick_scorer(rng,team)
            if p is None: continue
            a=pick_assist(rng,team,p); minute=rng.randint(1,45)
            events.append(MatchEvent(minute,'GOAL',name,p.name,a.name if a else None,f'⚽ {p.name} забивает!'))
            _goal_sequence(events,p,a,name,minute,positions,visual_rng)
            if a: events.append(MatchEvent(minute,'ASSIST',name,a.name,p.name,f'🅰️ Передача {a.name}.'))
    _add_open_play_events(events,home,away,ht,at,home_board,away_board,seed*17+3,3,44,hx,ax)
    _add_non_goal_shots(events,home,away,ht,at,home_board,away_board,seed*17+4,6,44,count=2)
    for _ in range(rng.randint(0,2)):
        team=home if rng.random()<.5 else away; name='HOME' if team is home else 'AWAY'; candidates=[x for x in team if x.position!='GK'] or list(team)
        if not candidates: continue
        p=rng.choice(candidates)
        events.append(MatchEvent(rng.randint(8,44),'YELLOW',name,p.name,text=f'🟨 {p.name} получает жёлтую.'))
    if rng.random()<0.025:
        team=home if rng.random()<.5 else away; name='HOME' if team is home else 'AWAY'
        candidates=[x for x in team if x.position!='GK'] or list(team)
        if candidates:
            p=rng.choice(candidates)
            events.append(MatchEvent(rng.randint(20,44),'RED',name,p.name,text=f'🟥 {p.name} удалён.'))
    if rng.random()<0.035:
        team=home if rng.random()<.5 else away; name='HOME' if team is home else 'AWAY'
        candidates=[x for x in team if x.position!='GK'] or list(team)
        if candidates:
            p=rng.choice(candidates)
            events.append(MatchEvent(rng.randint(10,44),'INJURY',name,p.name,text=f'🩹 {p.name} получил травму.'))
    events.sort(key=lambda e:(e.minute,_EVENT_ORDER.get(e.type,99)))
    return hs,as_,events

def simulate_first_half(home, away, ht, at, seed, home_counter=None, away_counter=None, home_board=None, away_board=None):
    hs,as_,events=_half_result(home,away,ht,at,seed,1,home_counter,away_counter,home_board,away_board)
    return hs,as_,events

def simulate_second_half(home, away, ht, at, seed, substitutions=None, home_counter=None, away_counter=None, home_board=None, away_board=None):
    """Deterministic minute-by-minute second half.

    Red cards/injuries are applied at their event minute, substitutions only when
    the outgoing player is active, and later minutes use the resulting squads.
    Empty sides are supported and simply cannot score.
    """
    substitutions=sorted(substitutions or [], key=lambda x:(x[0],x[1]))
    rng=Random(seed); visual_rng=Random(seed*31+46); events=[]; hs=as_=0
    home_now=list(home); away_now=list(away)
    sub_by_minute={}
    for item in substitutions:
        sub_by_minute.setdefault(int(item[0]),[]).append(item)
    # Generate incident schedule from the seed, then apply it in chronological order.
    incidents=[]
    for typ, chance, lo, hi in (('RED',.02,55,89),('INJURY',.035,50,88)):
        if rng.random() < chance:
            team_name='HOME' if rng.random()<.5 else 'AWAY'
            pool=home_now if team_name=='HOME' else away_now
            candidates=[p for p in pool if p.position!='GK'] or list(pool)
            if candidates:
                p=rng.choice(candidates)
                incidents.append((rng.randint(lo,hi),typ,team_name,p.id))
    incidents.sort(key=lambda x:(x[0], _EVENT_ORDER.get(x[1],99), x[3]))
    incidents_by_minute={}
    for item in incidents:
        incidents_by_minute.setdefault(item[0],[]).append(item)

    for minute in range(46,91):
        for _,off_id,on_player,team in sub_by_minute.get(minute,[]):
            pool=home_now if team=='HOME' else away_now
            idx=next((i for i,p in enumerate(pool) if p.id==off_id),None)
            if idx is not None and on_player.id not in {p.id for p in pool}:
                pool[idx]=on_player
                events.append(MatchEvent(minute,'SUBSTITUTION',team,on_player.name,None,
                    f'🔄 {on_player.name} выходит вместо #{off_id}.',{'visual_type':'SUBSTITUTION'}))
        for _,typ,team_name,pid in incidents_by_minute.get(minute,[]):
            pool=home_now if team_name=='HOME' else away_now
            idx=next((i for i,p in enumerate(pool) if p.id==pid),None)
            if idx is None:
                continue
            player=pool[idx]
            if typ=='RED':
                pool.pop(idx)
                events.append(MatchEvent(minute,'RED',team_name,player.name,None,f'🟥 {player.name} удалён.',{'visual_type':'CARD','card':'RED'}))
            else:
                pool.pop(idx)
                events.append(MatchEvent(minute,'INJURY',team_name,player.name,None,f'🩹 {player.name} получил травму.',{'visual_type':'INJURY'}))
        hfx,afx=expected_goals(home_now,away_now,ht,at,home_board=home_board,away_board=away_board,home_counter=home_counter,away_counter=away_counter)
        hfx=(hfx/45)*fatigue_factor_team(home_now,minute)
        afx=(afx/45)*fatigue_factor_team(away_now,minute)
        sh,sa=poisson(rng,hfx),poisson(rng,afx); hs+=sh; as_+=sa
        for team,name,n,pool in ((home_now,'HOME',sh,home_now),(away_now,'AWAY',sa,away_now)):
            positions=_player_map(pool,home_board if name=='HOME' else away_board,name)
            for _ in range(n):
                scorer=pick_scorer(rng,pool)
                if scorer is None:
                    continue
                assist=pick_assist(rng,pool,scorer)
                events.append(MatchEvent(minute,'GOAL',name,scorer.name,assist.name if assist else None,f'⚽ {scorer.name} забивает!'))
                _goal_sequence(events,scorer,assist,name,minute,positions,visual_rng)
                if assist:
                    events.append(MatchEvent(minute,'ASSIST',name,assist.name,scorer.name,f'🅰️ Передача {assist.name}.'))
        # A small deterministic sample of replay actions per 5-minute block.
        if minute % 5 == 0:
            _add_open_play_events(events,home_now,away_now,ht,at,home_board,away_board,seed*19+minute,minute,min(90,minute+4),hfx,afx,count=1)
            _add_non_goal_shots(events,home_now,away_now,ht,at,home_board,away_board,seed*23+minute,minute,min(90,minute+4),count=1)

    # Yellows are generated after incidents but never select a player who left the pitch.
    for _ in range(rng.randint(0,3)):
        team=home_now if rng.random()<.5 else away_now
        candidates=[x for x in team if x.position!='GK'] or list(team)
        if not candidates:
            continue
        p=rng.choice(candidates); name='HOME' if team is home_now else 'AWAY'
        events.append(MatchEvent(rng.randint(46,89),'YELLOW',name,p.name,text=f'🟨 {p.name} получает жёлтую.'))
    events.sort(key=lambda e:(e.minute,_EVENT_ORDER.get(e.type,99)))
    return hs,as_,events


def fatigue_factor_team(players, minute):
    if not players:
        return 0.0
    return sum(fatigue_factor(p, minute) for p in players) / len(players)

