from dataclasses import dataclass,field
import logging
import re
from random import Random, choice
from math import exp
from app.game.ratings import team_strength,player_rating,TeamStrength,WEIGHTS,effective_attributes,fatigue_adjusted_player
from app.game.tactics import Tactics,Style,tactical_modifiers,FORMATION_PROFILE,ALLOWED_FORMATIONS,style_matchup_modifiers
from app.game.talents import apply_talents
from app.game.ai_coach import adjust_tactics
from app.game.board import TacticalBoard, board_effect, phase_profile
from app.game.channels import channel_matchup
from app.game.brilliance import roll_brilliance,brilliance_xg_bonus
from app.game.weather import generate_weather,weather_fatigue_multiplier,weather_xg_modifier,weather_label
from app.game.ai_commentator import generate_comment
from app.game.match_event_schema import MatchVisualEvent

logger=logging.getLogger(__name__)

SHOT_TEMPLATES = [
    '🎯 {player} пробует пробить по воротам — момент!',
    '🔥 {player} решается на удар из опасной позиции!',
    '💥 {player} мощно бьёт по воротам!',
    '⚡ {player} наносит удар — защита под давлением!',
    '👀 {player} находит пространство и бьёт по воротам!',
]
FOUL_TEMPLATES = [
    '⚠️ {player} играет жёстко — судья фиксирует нарушение.',
    '🛑 Фол со стороны {player} — свисток арбитра!',
    '😤 {player} не успевает за соперником и нарушает правила.',
    '🚧 {player} останавливает атаку ценой нарушения.',
]
CORNER_TEMPLATES = [
    '🚩 Угловой! {player} готовится к подаче.',
    '🚩 {player} подходит к угловому флажку — шанс создать опасность.',
    '🚩 Угловой в пользу {club} — игроки идут в штрафную.',
    '📣 {player} подаёт с угла поля — команды готовятся к борьбе за мяч.',
]
GOAL_TEMPLATES = [
    '⚽ ГОЛ! {player} поражает ворота — {score}!',
    '🥅 Мяч в сетке! {player} отличился — на табло {score}.',
    '🚀 {player} забивает! Болельщики празднуют, счёт {score}.',
    '🎉 ГОЛ! {player} использует свой шанс — {score}.',
]
YELLOW_TEMPLATES = [
    '🟨 {player} получает жёлтую карточку — арбитр предупреждает игрока.',
    '🟨 Судья достаёт карточку: {player} теперь под предупреждением.',
    '⚠️ {player} наказан жёлтой — дальше нужно действовать осторожнее.',
]
RED_TEMPLATES = [
    '🟥 Красная карточка! {player} отправляется с поля.',
    '🚨 Удаление! {player} оставляет команду в меньшинстве.',
    '🟥 Арбитр удаляет {player} — серьёзный поворот матча.',
]
INJURY_TEMPLATES = [
    '🩹 {player} нуждается в помощи после эпизода.',
    '🚑 Пауза в игре: {player} получил повреждение.',
    '🩹 {player} не может сразу продолжить борьбу — медики выходят на поле.',
]
SUBSTITUTION_TEMPLATES = [
    '🔄 Замена: {player} выходит на поле и готов усилить команду.',
    '🔄 Тренер освежает состав — {player} вступает в игру.',
    '⬆️ {player} появляется на поле: свежие силы для команды.',
]
OFFSIDE_TEMPLATES = [
    '🚩 Офсайд! Атака {player} остановлена свистком.',
    '🚩 {player} забегает за линию защиты раньше времени — вне игры.',
    '⛔ Судья фиксирует положение вне игры у {player}.',
]

def _template_for_event(event):
    templates = {'SHOT': SHOT_TEMPLATES, 'FOUL': FOUL_TEMPLATES, 'CORNER': CORNER_TEMPLATES,
                 'GOAL': GOAL_TEMPLATES, 'YELLOW': YELLOW_TEMPLATES, 'RED': RED_TEMPLATES,
                 'RED_CARD': RED_TEMPLATES, 'INJURY': INJURY_TEMPLATES,
                 'SUBSTITUTION': SUBSTITUTION_TEMPLATES, 'OFFSIDE': OFFSIDE_TEMPLATES}
    variants = templates.get(event.type)
    if not variants:
        return event.text
    player = event.player or 'Игрок'
    club = event.team if event.team not in {'HOME', 'AWAY'} else ('хозяев' if event.team == 'HOME' else 'гостей')
    score = (event.metadata or {}).get('score', 'текущий счёт')
    # Stable per-event selection preserves deterministic match simulation.
    seed_text=f'{event.minute}|{event.type}|{event.team}|{player}|{event.text}'
    stable_seed=sum((i+1)*ord(ch) for i,ch in enumerate(seed_text))
    return Random(stable_seed).choice(variants).format(player=player, club=club, keeper='вратарь', score=score)

from app.game import roles as _roles, instructions as _instructions


def sanitize_event_text(text_value):
    """Normalize legacy text artifacts glued directly after a closing ')'."""
    value=str(text_value or '').replace('\\n','\n')
    return re.sub(r'\)(?=\S)\S*', ')', value).strip()


@dataclass
class MatchEvent:
    minute:int; type:str; team:str; player:str|None=None; secondary_player:str|None=None; text:str=''; metadata:dict=field(default_factory=dict); player_id:int|None=None; secondary_player_id:int|None=None

    def __post_init__(self):
        self.text=sanitize_event_text(self.text)
        self.text=_template_for_event(self)


def to_visual_event(match, event, sequence: int) -> MatchVisualEvent:
    """Convert an engine/persisted event into the stable visual-event contract.

    ``match`` and ``event`` may be SQLAlchemy mapping rows, dictionaries, or
    objects with attributes. Coordinates deliberately remain ``None`` until a
    future renderer/2D engine supplies them.
    """
    def value(obj, key, default=None):
        if obj is None:
            return default
        if hasattr(obj, 'get'):
            return obj.get(key, default)
        return getattr(obj, key, default)

    raw_type = str(value(event, 'type', value(event, 'event_type', 'PASS')) or 'PASS').upper()
    type_map = {
        'GOAL': 'GOAL', 'SHOT': 'SHOT', 'PASS': 'PASS', 'FOUL': 'FOUL',
        'CORNER': 'CORNER', 'YELLOW': 'YELLOW', 'YELLOW_CARD': 'YELLOW',
        'RED': 'RED', 'RED_CARD': 'RED', 'SUBSTITUTION': 'SUBSTITUTION',
        # The existing engine also emits replay-only actions. Normalize them to
        # a supported event type while retaining their specific outcome below.
        'CARRY': 'PASS', 'PRESSURE': 'FOUL', 'TACKLE': 'FOUL',
        'INTERCEPTION': 'PASS', 'OFFSIDE': 'FOUL', 'INJURY': 'FOUL',
    }
    event_type = type_map.get(raw_type, 'PASS')
    metadata = value(event, 'metadata', {}) or {}
    if not isinstance(metadata, dict):
        try:
            metadata = dict(metadata)
        except (TypeError, ValueError):
            metadata = {}

    explicit_team_id = value(event, 'team_id', value(event, 'club_id'))
    team = str(value(event, 'team', '') or '').upper()
    if explicit_team_id is not None:
        team_id = int(explicit_team_id)
    elif team in {'HOME', 'H'}:
        team_id = int(value(match, 'home_club_id', 0) or 0)
    elif team in {'AWAY', 'A'}:
        team_id = int(value(match, 'away_club_id', 0) or 0)
    else:
        team_id = 0

    player_id = value(event, 'player_id')
    if player_id is not None:
        player_id = int(player_id)
    minute = int(value(event, 'minute', 0) or 0)

    raw_outcome = metadata.get('outcome') or metadata.get('result')
    outcome_map = {
        'GOAL': 'goal', 'SHOT': 'shot', 'PASS': 'pass_complete',
        'FOUL': 'foul_committed', 'CORNER': 'corner_awarded',
        'YELLOW': 'yellow_card', 'YELLOW_CARD': 'yellow_card',
        'RED': 'red_card', 'RED_CARD': 'red_card',
        'SUBSTITUTION': 'substitution', 'CARRY': 'carry',
        'PRESSURE': 'pressure', 'TACKLE': 'tackle',
        'INTERCEPTION': 'interception', 'OFFSIDE': 'offside', 'INJURY': 'injury',
    }
    outcome = str(raw_outcome or outcome_map.get(raw_type, raw_type.lower()))
    score = metadata.get('score')
    home_score = value(match, 'home_score', 0)
    away_score = value(match, 'away_score', 0)
    if isinstance(score, str):
        import re
        found = re.search(r'(\d+)\s*[:\-–]\s*(\d+)', score)
        if found:
            home_score, away_score = int(found.group(1)), int(found.group(2))
    home_score = int(home_score or 0)
    away_score = int(away_score or 0)

    if event_type == 'GOAL':
        side = 'home' if team_id and team_id == int(value(match, 'home_club_id', -1) or -1) else 'away'
        commentary_key = f'goal_{side}'
    else:
        commentary_key = f"{event_type.lower()}_{'home' if team_id and team_id == int(value(match, 'home_club_id', -1) or -1) else 'away'}"

    return MatchVisualEvent(
        schema_version=1,
        match_id=int(value(match, 'id', value(match, 'match_id', 0)) or 0),
        sequence=int(sequence),
        minute=minute,
        type=event_type,
        team_id=team_id,
        player_id=player_id,
        outcome=outcome,
        home_score=home_score,
        away_score=away_score,
        commentary_key=commentary_key,
        coordinates=None,
    )


def _ai_comment(event, home_name, away_name, home_score, away_score):
    if event.type not in {'GOAL', 'RED', 'RED_CARD', 'INJURY', 'PENALTY'}:
        return
    club_name = event.team if event.team in {home_name, away_name} else (home_name if event.team == 'HOME' else away_name)
    opponent_name = away_name if club_name == home_name else home_name
    comment = generate_comment('RED_CARD' if event.type == 'RED' else event.type, {
        'event_type': event.type,
        'minute': int(event.minute),
        'player_name': event.player,
        'club_name': club_name,
        'opponent_name': opponent_name,
        'home_name': home_name,
        'away_name': away_name,
        'score': f'{home_score}:{away_score}',
        'match_minute_total': 90,
    })
    if comment and len(comment.strip()) >= 10:
        event.text = comment.strip()
    elif event.type in {'GOAL', 'RED', 'RED_CARD', 'INJURY', 'PENALTY'}:
        logger.warning('ai_commentator: fallback, reason=%s', 'empty_or_short_comment')
@dataclass
class MatchResult:
    home_score:int; away_score:int; home_xg:float; away_xg:float; events:list[MatchEvent]=field(default_factory=list); ratings:dict[str,float]=field(default_factory=dict); man_of_the_match:str|None=None; extra_time_played:bool=False; final_minute:int=90


_EVENT_ORDER={'PASS':0,'CARRY':1,'TACTICAL_CHANGE':2,'PRESSURE':3,'TACKLE':4,'INTERCEPTION':4,'MOMENT_OF_BRILLIANCE':5,'SHOT':6,'GOAL':7,'ASSIST':8,'FOUL':9,'YELLOW':10,'RED':11,'INJURY':12,'SUBSTITUTION':13,'WEATHER':14}

def _player_map(players, board, team):
    slots=list(board.slots) if board is not None else list(TacticalBoard(players[0].position if False else '4-3-3').slots)
    by_id={}
    for i,p in enumerate(players):
        slot=slots[i] if i<len(slots) else slots[-1]
        # x is lateral and y is depth. Away sides mirror depth so both attack toward y=100.
        x=float(slot.x)
        y=100-slot.y if team=='AWAY' else slot.y
        by_id[p.id]=(x,y)
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
            events.append(MatchEvent(minute,'PASS',side,actor.name,receiver.name,f'↗️ {actor.name} → {receiver.name}',_visual_meta('PASS',start,end,zone='BUILD_UP' if start[1]<50 else 'ATTACK')))
        elif kind=='CARRY':
            direction=1 if side=='HOME' else -1
            end=[max(3,min(97,start[0]+rng.uniform(-9,9))),max(4,min(96,start[1]+direction*rng.uniform(6,15)))]
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

CLAMP_XG=(0.0,3.0)

def clamp_xg(value):
    return max(CLAMP_XG[0],min(CLAMP_XG[1],float(value)))

def _fatigue_drain_per_minute(tactics):
    if tactics is None:
        return 0.45
    return 0.25 + 0.0025*float(tactics.pressing) + 0.0015*float(tactics.tempo)

def _initial_fatigue(players, tactics, minutes=45, weather='CLEAR'):
    drain=_fatigue_drain_per_minute(tactics)*weather_fatigue_multiplier(weather)*max(0,int(minutes))
    return {int(p.id):min(100.0,drain) for p in players if getattr(p,'id',None) is not None}

def _advance_fatigue(fatigue, players, tactics, weather='CLEAR'):
    drain=_fatigue_drain_per_minute(tactics)*weather_fatigue_multiplier(weather)
    for player in players:
        pid=getattr(player,'id',None)
        if pid is None:
            continue
        fatigue[int(pid)]=min(100.0,float(fatigue.get(int(pid),0.0))+drain)

def _urgency_mods(tactics, context):
    if not context or tactics is None:
        return 1.0,1.0
    minute=int(context.get('minute') or 0)
    diff=int(context.get('score_diff') or 0)
    if diff<0 and minute>60:
        return 1.05,0.97
    if diff>0 and minute>75:
        return 0.98,1.03
    if diff==0 and minute>80:
        return 1.02,1.0
    return 1.0,1.0

def poisson(rng,lam):
    if lam <= 0:
        return 0
    lam=min(lam,4.5); L=exp(-lam); k=0;p=1
    while p>L: k+=1;p*=rng.random()
    return min(5,k-1)
def formation_matchup(own_formation, opp_formation):
    """Small structural matchup modifier, deliberately capped to ±8%."""
    own = FORMATION_PROFILE[own_formation]
    opp = FORMATION_PROFILE[opp_formation]
    midfield = 1.0 + (own['mid'] - opp['mid']) * 0.008
    width = 1.0 + (own['wide'] - opp['wide']) * 0.006
    central = 1.0 + (own['am'] - opp['dm']) * 0.006 - (opp['am'] - own['dm']) * 0.004
    defensive = 1.0 - (opp['def'] - own['def']) * 0.080
    return max(0.92, min(1.08, midfield * width * central * defensive))


# Backward-compatible private name used by older callers/tests.
_formation_matchup = formation_matchup


def defensive_density(formation, attacking_formation=None):
    """Relative defensive density. 1.0 is a neutral four-defender baseline."""
    profile = FORMATION_PROFILE[formation]
    value = 1.0 + (profile['def'] - 4) * 0.080 + (profile['dm'] - 1) * 0.012
    if attacking_formation:
        attack = FORMATION_PROFILE[attacking_formation]
        value += (profile['def'] - attack['att']) * 0.006
    return max(0.92, min(1.08, value))


def _channel_rating(players, positions, board, fatigue=None):
    values=[]
    fatigue=fatigue or {}
    for i,p in enumerate(players):
        if p.position in positions:
            slot=board.slots[i].slot if i < len(board.slots) else p.position
            adjusted=fatigue_adjusted_player(p,fatigue.get(int(getattr(p,'id',0)),0.0))
            values.append(player_rating(adjusted, slot))
    return sum(values)/len(values) if values else None


def _channel_matchup(attackers, defenders, attack_positions, defense_positions, attack_board, defense_board, fallback_attack, fallback_defense, attack_fatigue=None, defense_fatigue=None):
    ar=_channel_rating(attackers, attack_positions, attack_board, attack_fatigue) or fallback_attack
    dr=_channel_rating(defenders, defense_positions, defense_board, defense_fatigue) or fallback_defense
    return max(.70, min(1.35, (ar/max(1,dr))**.9))


def _position_channel_factors(home, away, hb, ab, home_fatigue=None, away_fatigue=None):
    # Channels are expressed from each team's attacking perspective.
    home_fatigue=home_fatigue or {}; away_fatigue=away_fatigue or {}
    h_attack=sum(player_rating(fatigue_adjusted_player(p,home_fatigue.get(int(getattr(p,'id',0)),0.0)), hb.slots[i].slot if i < len(hb.slots) else None) for i,p in enumerate(home))/len(home)
    a_attack=sum(player_rating(fatigue_adjusted_player(p,away_fatigue.get(int(getattr(p,'id',0)),0.0)), ab.slots[i].slot if i < len(ab.slots) else None) for i,p in enumerate(away))/len(away)
    h_def=sum(player_rating(fatigue_adjusted_player(p,home_fatigue.get(int(getattr(p,'id',0)),0.0)), hb.slots[i].slot if i < len(hb.slots) else None) for i,p in enumerate(home))/len(home)
    a_def=sum(player_rating(fatigue_adjusted_player(p,away_fatigue.get(int(getattr(p,'id',0)),0.0)), ab.slots[i].slot if i < len(ab.slots) else None) for i,p in enumerate(away))/len(away)
    channels_home=(
        _channel_matchup(home, away, {'LW','LM'}, {'RB','RM'}, hb, ab, h_attack, a_def, home_fatigue, away_fatigue),
        _channel_matchup(home, away, {'ST','AM'}, {'CB','DM'}, hb, ab, h_attack, a_def, home_fatigue, away_fatigue),
        _channel_matchup(home, away, {'RW','RM'}, {'LB','LM'}, hb, ab, h_attack, a_def, home_fatigue, away_fatigue),
    )
    channels_away=(
        _channel_matchup(away, home, {'LW','LM'}, {'RB','RM'}, ab, hb, a_attack, h_def, away_fatigue, home_fatigue),
        _channel_matchup(away, home, {'ST','AM'}, {'CB','DM'}, ab, hb, a_attack, h_def, away_fatigue, home_fatigue),
        _channel_matchup(away, home, {'RW','RM'}, {'LB','LM'}, ab, hb, a_attack, h_def, away_fatigue, home_fatigue),
    )
    # Three channel xG values are base_xg/3 * factor; their sum is base_xg * average(factor).
    return channels_home, channels_away


def _role_player_rating(player, slot, fatigue=0.0):
    """Apply only the structural role layer on top of the legacy rating.

    Unknown/STARTER roles deliberately return the legacy player_rating result.
    role_effective_stats() currently uses a 0.72 mismatch fit; Iteration 2
    tightens that existing mismatch effect to a net 0.65 role-fit coefficient,
    keeping out-of-position role benefits meaningful but clearly weaker than a
    natural fit. The final role multiplier is capped at +/-8%.
    """
    effective_player=fatigue_adjusted_player(player,fatigue)
    base=player_rating(effective_player, slot.slot if slot is not None else None)
    role=getattr(slot, 'role', None) if slot is not None else None
    if not role or role == 'STARTER' or role not in _roles.ROLE_PROFILES:
        return base
    position=getattr(slot, 'slot', None)
    adjusted=_roles.role_effective_stats(effective_player, role, position)
    raw_fit=float(adjusted.get('role_fit', 1.0))
    target_fit=1.0 if raw_fit >= 0.99 else 0.65
    stat_names=('pace','shooting','passing','dribbling','defending','physical','stamina','mental')
    attrs={name:float(getattr(effective_player,name,0) or 0) for name in stat_names}
    if not attrs:
        return base
    weighted=sum(attrs[name]*weight for name,weight in zip(attrs, WEIGHTS[player.position]))
    if weighted <= 0:
        return base
    adjusted_weighted=weighted
    for name in ('shooting','passing','defending','stamina'):
        original=attrs[name]
        if original <= 0:
            continue
        delta=(float(adjusted.get(name, original))-original)
        if raw_fit > 0:
            delta *= target_fit/raw_fit
        adjusted_weighted += delta * WEIGHTS[player.position][('pace','shooting','passing','dribbling','defending','physical','stamina','mental').index(name)]
    role_multiplier=max(.92,min(1.08,adjusted_weighted/weighted))
    return round(max(1,min(100,base*role_multiplier)),1)


def _instruction_phase_multiplier(slots, phase):
    """Return one <=8% instruction multiplier for a single strength phase."""
    relevant=[]
    for slot in slots:
        instruction=getattr(slot,'instruction','BALANCED')
        if instruction == 'BALANCED' or instruction not in _instructions.INSTRUCTION_EFFECTS:
            continue
        values=_instructions.apply_instruction(
            {'attack':1.0,'defence':1.0,'stamina':1.0,'width':1.0,'risk':0.0},
            instruction,
        )
        if phase == 'att':
            value=values['attack']
        elif phase == 'def':
            value=values['defence']
        else:
            value=(values['attack']+values['defence'])/2
        relevant.append(value)
    if not relevant:
        return 1.0
    return max(.92,min(1.08,sum(relevant)/len(relevant)))


def _slider_phase_multipliers(tactics, opponent_tactics=None):
    """Translate tactical sliders into bounded phase multipliers."""
    if tactics is None:
        return 1.0, 1.0, 1.0
    tempo=1 + (tactics.tempo-50)/50*.06
    pressing=1 + (tactics.pressing-50)/50*.05
    aggression=1 + (tactics.aggression-50)/50*.04
    defensive_line=1 + (tactics.defensive_line-50)/50*.05
    # Opponent pressing makes our defensive phase less effective: higher pressure
    # means more difficulty playing out and defending, not a defensive bonus.
    opponent_pressing=1 - ((opponent_tactics.pressing-50)/50*.05) if opponent_tactics is not None else 1.0
    return tempo*pressing, pressing*aggression, defensive_line*opponent_pressing

def _engine_team_strength(players, formation, board=None, tactics=None, opponent_tactics=None, match_context=None, side=None):
    """Legacy team strength plus bounded role/instruction/slider/style effects."""
    effective_board=board or TacticalBoard(formation)
    has_structural_effect=any(
        getattr(slot,'role','STARTER') in _roles.ROLE_PROFILES or
        getattr(slot,'instruction','BALANCED') != 'BALANCED'
        for slot in effective_board.slots
    )
    if board is None and tactics is None and not has_structural_effect:
        return team_strength(players, formation, board)
    if not players:
        return team_strength(players, formation, board)
    if len(players)>11:
        raise ValueError('A match lineup cannot contain more than 11 players.')
    atk=[]; mid=[]; de=[]; gk=[]
    context=dict(match_context or {})
    fatigue=context.get(f'{side}_fatigue',{}) if side else {}
    morale=context.get(f'{side}_morale',{}) if side else {}
    for i,p in enumerate(players):
        slot=effective_board.slots[i] if i < len(effective_board.slots) else None
        fatigue_value=float(fatigue.get(int(p.id),0.0)) if fatigue else 0.0
        rating=_role_player_rating(p,slot,fatigue_value)
        if p.position in {'ST','LW','RW','AM'}: atk.append(rating)
        if p.position in {'DM','CM','AM'}: mid.append(rating)
        if p.position in {'CB','LB','RB','DM'}: de.append(rating)
        if p.position=='GK': gk.append(rating)
    avg=lambda xs: sum(xs)/len(xs) if xs else 50
    availability=.55 + .45*(len(players)/11)
    baseline_a=avg(atk)*availability
    baseline_m=avg(mid)*availability
    baseline_d=avg(de)*availability
    g=avg(gk)*availability
    instruction_a=_instruction_phase_multiplier(effective_board.slots,'att')
    instruction_m=_instruction_phase_multiplier(effective_board.slots,'mid')
    instruction_d=_instruction_phase_multiplier(effective_board.slots,'def')
    slider_a,slider_m,slider_d=_slider_phase_multipliers(tactics,opponent_tactics)
    style_a,style_m,style_d=style_matchup_modifiers(tactics.style,opponent_tactics.style) if tactics is not None and opponent_tactics is not None else (1.0,1.0,1.0)
    talent_context={
        'home':context.get('home'), 'away':context.get('away'),
        'minute':int(context.get('minute') or 0),
        'home_score':int(context.get('home_score') or 0),
        'away_score':int(context.get('away_score') or 0),
        'score_diff':int(context.get('score_diff') or 0),
        'side':side or context.get('side'),
    }
    talent_a,talent_m,talent_d=apply_talents(players,tactics,opponent_tactics,talent_context)
    urgency_a,urgency_d=_urgency_mods(tactics,context)
    a=baseline_a*instruction_a*slider_a*style_a*talent_a*urgency_a
    m=baseline_m*instruction_m*slider_m*style_m*talent_m
    d=baseline_d*instruction_d*slider_d*style_d*talent_d*urgency_d
    a=max(.85*baseline_a,min(1.15*baseline_a,a))
    m=max(.85*baseline_m,min(1.15*baseline_m,m))
    d=max(.85*baseline_d,min(1.15*baseline_d,d))
    return TeamStrength(a,m,d,g,.38*a+.27*m+.27*d+.08*g)


def expected_goals(home,away,ht,at,home_board=None,away_board=None,home_counter=None,away_counter=None,match_context=None):
    if not home or not away:
        return 0.0, 0.0
    # Conversion is applied only when Poisson goals are generated; xG itself is unchanged.
    context=dict(match_context or {})
    home_context={**context,'score_diff':int(context.get('home_score',0))-int(context.get('away_score',0)),'side':'home'}
    away_context={**context,'score_diff':int(context.get('away_score',0))-int(context.get('home_score',0)),'side':'away'}
    hs,as_=_engine_team_strength(home,ht.formation,home_board,ht,at,home_context,'home'),_engine_team_strength(away,at.formation,away_board,at,ht,away_context,'away'); ha,hd=tactical_modifiers(ht); aa,ad=tactical_modifiers(at)
    he=board_effect(home_board or TacticalBoard(ht.formation),ht.style,ht.pressing,ht.width,ht.defensive_line); ae=board_effect(away_board or TacticalBoard(at.formation),at.style,at.pressing,at.width,at.defensive_line)
    hb=home_board or TacticalBoard(ht.formation); ab=away_board or TacticalBoard(at.formation)
    he=board_effect(hb,ht.style,ht.pressing,ht.width,ht.defensive_line); ae=board_effect(ab,at.style,at.pressing,at.width,at.defensive_line)
    hp=phase_profile(hb,ht.style,ht.pressing,ht.width,ht.defensive_line); ap=phase_profile(ab,at.style,at.pressing,at.width,at.defensive_line)
    # Phase matchups: build-up under pressure, possession versus press, and transition space.
    hx_phase=hp['build_up']*max(.90,1+(hp['possession']-ap['pressing'])*.45)
    ax_phase=ap['build_up']*max(.90,1+(ap['possession']-hp['pressing'])*.45)
    h_transition=hp['transition_attack']
    a_transition=ap['transition_attack']
    weather=str(context.get('weather') or 'CLEAR').upper()
    weather_mod=weather_xg_modifier(weather)
    if at.defensive_line>65: hx_phase*=h_transition
    if ht.defensive_line>65: ax_phase*=a_transition
    hx_phase *= weather_mod['passing'] * weather_mod['tempo']
    ax_phase *= weather_mod['passing'] * weather_mod['tempo']
    hx_phase*=hp['width']/max(.92,ap['width'])
    ax_phase*=ap['width']/max(.92,hp['width'])
    h_at=(.52*hs.attack+.30*hs.midfield+.18*hs.overall)*ha*he['central_control']*he['chance_creation']
    a_at=(.52*as_.attack+.30*as_.midfield+.18*as_.overall)*aa*ae['central_control']*ae['chance_creation']
    h_de=(.60*hs.defense+.40*hs.goalkeeper)*hd*he['transition_def']
    a_de=(.60*as_.defense+.40*as_.goalkeeper)*ad*ae['transition_def']
    # Iteration 1: apply only a small structural defensive-density effect.
    h_de *= defensive_density(ht.formation, at.formation)
    a_de *= defensive_density(at.formation, ht.formation)
    # Iteration 7: compress the attack/defence ratio before venue/tactical
    # effects are applied. This keeps the existing I2-I5 layers active while
    # preventing multiplicative strength gaps from exploding xG.
    base_xg=2.30
    def _soft_xg(raw):
        ratio=max(0.35,float(raw)/base_xg)
        total_multiplier=max(0.65,min(1.85,ratio**0.85))
        return base_xg*total_multiplier
    hx=_soft_xg(h_at/max(1,a_de))
    ax=_soft_xg(a_at/max(1,h_de))
    # Player quality remains the primary separator after tactical compression.
    raw_home_quality=team_strength(home,ht.formation).overall
    raw_away_quality=team_strength(away,at.formation).overall
    quality_ratio=max(.70,min(1.43,(max(1.0,raw_home_quality)/max(1.0,raw_away_quality))**0.85))
    hx *= quality_ratio
    ax /= quality_ratio
    h_talent_attack=apply_talents(home,ht,at,home_context)[0]
    a_talent_attack=apply_talents(away,at,ht,away_context)[0]
    hx *= h_talent_attack
    ax *= a_talent_attack
    # Softer systematic home advantage.
    hx *= 1.15; ax *= 0.85
    # Formation-level matchups: midfield control, defensive density and width.
    hx *= formation_matchup(ht.formation, at.formation)
    ax *= formation_matchup(at.formation, ht.formation)
    hx /= max(0.98, defensive_density(at.formation, ht.formation) ** 0.55)
    ax /= max(0.98, defensive_density(ht.formation, at.formation) ** 0.55)
    # Position-vs-position channels remain the existing player-quality layer.
    channel_home, channel_away = _position_channel_factors(home, away, hb, ab, context.get('home_fatigue'), context.get('away_fatigue'))
    def _width_channels(channels, width):
        shift=(width-50)/50*.05
        return tuple(value*(1-shift if index==1 else 1+shift) for index,value in enumerate(channels))
    channel_home=_width_channels(channel_home,ht.width)
    channel_away=_width_channels(channel_away,at.width)
    hx *= sum(channel_home) / len(channel_home)
    ax *= sum(channel_away) / len(channel_away)
    # Iteration 1 structural channel effect: small and capped at ±8%.
    structural = channel_matchup(hb, ab)
    h_channel = max(.98, min(1.02, 1.0 + (structural['CENTER'] - 1.0) * .10))
    a_channel = max(.98, min(1.02, 1.0 + ((1.0 / structural['CENTER']) - 1.0) * .10))
    hx *= h_channel
    ax *= a_channel
    hx *= hx_phase; hx *= 1 + min(.05, (he['width_attack']-1)*.8)
    ax *= ax_phase; ax *= 1 + min(.05, (ae['width_attack']-1)*.8)
    if ht.style==Style.COUNTER and at.defensive_line>65: hx*=1.05
    if at.style==Style.COUNTER and ht.defensive_line>65: ax*=1.05
    if ht.pressing>70 and at.style==Style.POSSESSION: hx*=1.03
    if at.pressing>70 and ht.style==Style.POSSESSION: ax*=1.03
    hx *= weather_mod['shooting']
    ax *= weather_mod['shooting']
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
    return round(clamp_xg(hx),2),round(clamp_xg(ax),2)
def pick_scorer(rng,players):
    xs=[p for p in players if p.position!='GK' and not p.suspended]
    if not xs:
        return None
    return rng.choices(xs,weights=[max(1,player_rating(p))*(1.25 if p.position=='ST' else 1) for p in xs],k=1)[0]
def pick_assist(rng,players,scorer):
    xs=[p for p in players if p.id!=scorer.id and p.position!='GK' and not p.suspended]; return rng.choice(xs) if xs else None
def _corner_count(tactics, phase):
    """Return the deterministic number of corners for one team in one half."""
    base=2
    base += int(tactics.width>65)
    base += int(tactics.pressing>65)
    base += int(tactics.style in {Style.ATTACK,Style.POSSESSION})
    return max(0,min(5,base))

def _add_corner_events(events, home_players, away_players, home_count, away_count, seed, minute_start, minute_end):
    rng=Random(seed)
    for team,side,count in ((home_players,'HOME',home_count),(away_players,'AWAY',away_count)):
        candidates=[p for p in team if p.position!='GK'] or list(team)
        for _ in range(count):
            if not candidates:
                continue
            player=rng.choice(candidates)
            minute=rng.randint(minute_start,minute_end)
            events.append(MatchEvent(minute,'CORNER',side,player.name,None,f'🚩 Угловой {side}',{},player.id,None))


def _add_foul_events(events, home_players, away_players, seed, minute_start, minute_end, half=None):
    """Generate deterministic foul events with player/minute attribution."""
    rng=Random(seed)
    if half is None:
        counts=(8+((int(seed)+0)%7),8+((int(seed)+1)%7))
    else:
        counts=(4+((int(seed)+int(half)*3+0)%4),4+((int(seed)+int(half)*3+1)%4))
    for team,side,count in ((home_players,'HOME',counts[0]),(away_players,'AWAY',counts[1])):
        candidates=[p for p in team if p.position!='GK'] or list(team)
        if not candidates:
            continue
        for _ in range(count):
            player=rng.choice(candidates)
            minute=rng.randint(minute_start,minute_end)
            events.append(MatchEvent(minute,'FOUL',side,player.name,None,f'⚠️ {player.name} нарушает правила.',{},player.id,None))


def conversion_modifier(tactics):
    if tactics.style==Style.COUNTER:
        return 1.08
    if tactics.style==Style.POSSESSION:
        return 0.96
    return 1.0

def _sample_half_goals(rng, home_xg, away_xg, home_tactics, away_tactics, max_goals=5):
    """Sample one 45-minute goal total, then distribute it by effective xG.

    Live used to call Poisson(xG/45) once per minute. That repeatedly sampled
    the same full-match strength and allowed fatigue/morale/context multipliers
    to inflate the cumulative rate. A half now gets exactly one Poisson draw
    from its 45-minute xG budget.
    """
    home_lambda=max(0.0,float(home_xg))*conversion_modifier(home_tactics)
    away_lambda=max(0.0,float(away_xg))*conversion_modifier(away_tactics)
    total_lambda=home_lambda+away_lambda
    total=min(int(max_goals),poisson(rng,total_lambda))
    if total<=0 or total_lambda<=0:
        return []
    home_weight=home_lambda/total_lambda
    return [
        ('HOME' if rng.random()<home_weight else 'AWAY', minute)
        for minute in sorted(rng.sample(range(2,46),k=min(total,44)))
    ]

def _shots_from_xg(xg, goals, rng):
    total=max(goals,2,round(xg*8+rng.uniform(-2,2)))
    on_target=max(goals,round(total*0.35))
    on_target=min(total,on_target)
    return total,on_target

def _add_shot_events(events, home, away, ht, at, home_board, away_board, seed, minute_start, minute_end,
                     home_total, away_total, home_on_target, away_on_target):
    rng=Random(seed)
    hp=_player_map(home,home_board or TacticalBoard(ht.formation),'HOME'); ap=_player_map(away,away_board or TacticalBoard(at.formation),'AWAY')
    specs=[(home,'HOME',hp,home_total,home_on_target),(away,'AWAY',ap,away_total,away_on_target)]
    for team,side,positions,total,on_target in specs:
        existing=sum(1 for e in events if e.type=='SHOT' and e.team==side and e.metadata.get('result')=='GOAL')
        non_goal_total=max(0,total-existing)
        non_goal_on=max(0,on_target-existing)
        non_goal_on=min(non_goal_total,non_goal_on)
        pool=[p for p in team if p.position!='GK'] or list(team)
        if not pool:
            continue
        target_indices=set(rng.sample(range(non_goal_total),k=non_goal_on)) if non_goal_on else set()
        for i in range(non_goal_total):
            shooter=_choose_player(rng,pool,weights=lambda p: 1.8 if p.position=='ST' else (1.35 if p.position in {'LW','RW','AM'} else 1.0))
            if not shooter: continue
            start=_event_point(rng,positions.get(shooter.id,(75,50)),3)
            end=[100 if side=='HOME' else 0,round(max(15,min(85,50+rng.uniform(-28,28))),1)]
            result='SAVED' if i in target_indices else rng.choice(['BLOCKED','WIDE'])
            minute=rng.randint(minute_start,minute_end)
            events.append(MatchEvent(minute,'SHOT',side,shooter.name,None,f'🎯 {shooter.name} наносит удар',_visual_meta('SHOT',start,end,result,zone='FINISH'),shooter.id,None))


def simulate_match(home_name,away_name,home,away,home_tactics,away_tactics,seed,home_board=None,away_board=None,home_counter=None,away_counter=None,allow_extra_time=False):
    if len(home)!=11 or len(away)!=11: raise ValueError('Each side must have 11 starting players.')
    rng=Random(seed); weather=generate_weather(Random(seed*1009+1)); context={'weather':weather}; hx,ax=expected_goals(home,away,home_tactics,away_tactics,home_board,away_board,home_counter,away_counter,context); events=[]
    brilliance_rng=Random(seed*1013+3)
    for team,side in ((home,'HOME'),(away,'AWAY')):
        candidates=list(team)
        for player in candidates:
            if roll_brilliance(player,brilliance_rng):
                bonus=brilliance_xg_bonus(brilliance_rng); hx_bonus=bonus if side=='HOME' else 0.0; ax_bonus=bonus if side=='AWAY' else 0.0
                if side=='HOME': hx += bonus
                else: ax += bonus
                minute=brilliance_rng.randint(8,88)
                events.append(MatchEvent(minute,'MOMENT_OF_BRILLIANCE',side,player.name,None,f'✨ Гениальный момент: {player.name} создает шанс!',{'xg_bonus':bonus,'weather':weather},player.id,None))
                logger.info('✨ Гениальный момент: %s создает шанс!',player.name)
                break
    hs,as_=poisson(rng,hx*conversion_modifier(home_tactics)),poisson(rng,ax*conversion_modifier(away_tactics))
    visual_rng=Random(seed*31+7)
    events.append(MatchEvent(1,'WEATHER','HOME',None,None,weather_label(weather),{'weather':weather}))
    home_pos=_player_map(home,home_board or TacticalBoard(home_tactics.formation),'HOME')
    away_pos=_player_map(away,away_board or TacticalBoard(away_tactics.formation),'AWAY')
    for team,name,n,positions in ((home,home_name,hs,home_pos),(away,away_name,as_,away_pos)):
        for _ in range(n):
            p=pick_scorer(rng,team)
            if p is None: continue
            a=pick_assist(rng,team,p); minute=rng.randint(2,90)
            events.append(MatchEvent(minute,'GOAL',name,p.name,a.name if a else None,f'⚽ {p.name} забивает!',{},p.id,a.id if a else None))
            _ai_comment(events[-1], home_name, away_name, hs, as_)
            _goal_sequence(events,p,a,'HOME' if team is home else 'AWAY',minute,positions,visual_rng)
            if a: events.append(MatchEvent(minute,'ASSIST',name,a.name,p.name,f'🅰️ Передача {a.name}.',{},a.id,p.id))
    home_total,home_on_target=_shots_from_xg(hx,hs,rng); away_total,away_on_target=_shots_from_xg(ax,as_,rng)
    _add_open_play_events(events,home,away,home_tactics,away_tactics,home_board,away_board,seed*17+1,4,88,hx,ax)
    _add_shot_events(events,home,away,home_tactics,away_tactics,home_board,away_board,seed*17+2,7,88,home_total,away_total,home_on_target,away_on_target)
    _add_corner_events(events,home,away,_corner_count(home_tactics,'first'),_corner_count(away_tactics,'first'),seed*17+5,8,44)
    _add_corner_events(events,home,away,_corner_count(home_tactics,'second'),_corner_count(away_tactics,'second'),seed*17+6,46,88)
    _add_foul_events(events,home,away,seed*17+7,4,88)
    for minute in sorted(rng.sample(range(8,89),k=rng.randint(1,4))):
        team=home if rng.random()<.5 else away;p=rng.choice(team)
        if p.position!='GK':
            events.append(MatchEvent(minute,'YELLOW',home_name if p in home else away_name,p.name,text=f'🟨 {p.name} получает жёлтую.'))
            _ai_comment(events[-1], home_name, away_name, hs, as_)
    injury_time=Random(int(seed)*7919+90).randint(1,5) if allow_extra_time else 0
    if allow_extra_time:
        stoppage_rng=Random(int(seed)*15485863+90)
        for added in range(1,injury_time+1):
            minute=90+added
            for team_name,pool in (('HOME',home),('AWAY',away)):
                candidates=[p for p in pool if getattr(p,'position',None)!='GK'] or list(pool)
                if candidates and stoppage_rng.random()<0.012:
                    scorer=stoppage_rng.choice(candidates)
                    assist=pick_assist(stoppage_rng,pool,scorer)
                    events.append(MatchEvent(minute,'GOAL',team_name,scorer.name,assist.name if assist else None,
                        f'90+{added}′ ⚽ Гол в добавленное время: {scorer.name}!',
                        {'is_added_time':True,'added_minute':added},getattr(scorer,'id',None),getattr(assist,'id',None) if assist else None))
                    if team_name=='HOME': hs+=1
                    else: as_+=1
                    if assist:
                        events.append(MatchEvent(minute,'ASSIST',team_name,assist.name,scorer.name,
                            f'90+{added}′ 🅰️ Передача {assist.name}.',{'is_added_time':True,'added_minute':added},
                            getattr(assist,'id',None),getattr(scorer,'id',None)))
                elif candidates and stoppage_rng.random()<0.45:
                    player=stoppage_rng.choice(candidates)
                    events.append(MatchEvent(minute,'SHOT',team_name,player.name,None,
                        f'90+{added}′ {player.name} бьёт в добавленное время.',
                        {'is_added_time':True,'added_minute':added},getattr(player,'id',None),None))
    extra_time_played=bool(allow_extra_time and hs==as_)
    final_minute=90+injury_time
    if extra_time_played:
        et_rng=Random(int(seed)*104729+120)
        for et_minute in range(1,31):
            minute=90+injury_time+et_minute
            for team_name,pool in (('HOME',home),('AWAY',away)):
                if not pool or et_rng.random() >= 0.018:
                    continue
                candidates=[p for p in pool if getattr(p,'position',None)!='GK'] or list(pool)
                if not candidates: continue
                scorer=et_rng.choice(candidates)
                assist=pick_assist(et_rng,pool,scorer)
                events.append(MatchEvent(minute,'GOAL',team_name,scorer.name,assist.name if assist else None,
                    f'{90+et_minute}′ ⚽ Гол в дополнительное время: {scorer.name}!',
                    {'extra_time':True,'display_minute':90+et_minute,'extra_time_minute':et_minute},
                    getattr(scorer,'id',None),getattr(assist,'id',None) if assist else None))
                if team_name=='HOME': hs+=1
                else: as_+=1
                if assist:
                    events.append(MatchEvent(minute,'ASSIST',team_name,assist.name,scorer.name,
                        f'{90+et_minute}′ 🅰️ Передача {assist.name}.',
                        {'extra_time':True,'display_minute':90+et_minute,'extra_time_minute':et_minute},
                        getattr(assist,'id',None),getattr(scorer,'id',None)))
        final_minute=90+injury_time+30
    events.sort(key=lambda e:(e.minute,_EVENT_ORDER.get(e.type,99)))
    ratings={}
    for p in home+away:
        r=6.2+(player_rating(p)-65)*.055+.15*(p.position=='GK')
        r += .85*sum(e.type=='GOAL' and e.player==p.name for e in events); r+=.45*sum(e.type=='ASSIST' and e.player==p.name for e in events); ratings[p.name]=round(max(5,min(10,r)),1)
    motm=max(ratings,key=ratings.get); return MatchResult(hs,as_,hx,ax,events,ratings,motm,extra_time_played,final_minute)


def _half_result(home, away, ht, at, seed, half, home_counter=None, away_counter=None, home_board=None, away_board=None, weather=None, brilliance_excluded_ids=None):
    rng=Random(seed)
    weather=weather or generate_weather(Random(seed*1009+1))
    brilliance_excluded_ids=set(brilliance_excluded_ids or ())
    events=[]; visual_rng=Random(seed*31+half)
    events.append(MatchEvent(1,'WEATHER','HOME',None,None,weather_label(weather),{'weather':weather}))
    home_pos=_player_map(home,home_board or TacticalBoard(ht.formation),'HOME')
    away_pos=_player_map(away,away_board or TacticalBoard(at.formation),'AWAY')
    context={
        'minute':0,'home_score':0,'away_score':0,
        'home':home,'away':away,
        'home_fatigue':{int(p.id):0.0 for p in home if getattr(p,'id',None) is not None},
        'away_fatigue':{int(p.id):0.0 for p in away if getattr(p,'id',None) is not None},
        'home_morale':{int(p.id):50.0 for p in home if getattr(p,'id',None) is not None},
        'away_morale':{int(p.id):50.0 for p in away if getattr(p,'id',None) is not None},
        'weather':weather,
    }
    hfx,afx=expected_goals(
        home,away,ht,at,
        home_board=home_board,away_board=away_board,
        home_counter=home_counter,away_counter=away_counter,
        match_context=context
    )
    brilliance_rng=Random(seed*1013+half)
    for team,side in ((home,'HOME'),(away,'AWAY')):
        for player in team:
            if getattr(player,'id',None) in brilliance_excluded_ids:
                continue
            if roll_brilliance(player,brilliance_rng):
                bonus=brilliance_xg_bonus(brilliance_rng)
                if side=='HOME': hfx += bonus
                else: afx += bonus
                minute=brilliance_rng.randint(8,44)
                events.append(MatchEvent(minute,'MOMENT_OF_BRILLIANCE',side,player.name,None,f'✨ Гениальный момент: {player.name} создает шанс!',{'xg_bonus':bonus,'weather':weather},player.id,None))
                logger.info('✨ Гениальный момент: %s создает шанс!',player.name)
                break
    goals=_sample_half_goals(rng,hfx*.48,afx*.48,ht,at,max_goals=5)
    for name,minute in goals:
        team=home if name=='HOME' else away
        positions=home_pos if name=='HOME' else away_pos
        scorer=pick_scorer(rng,team)
        if scorer is None:
            continue
        assist=pick_assist(rng,team,scorer)
        events.append(MatchEvent(minute,'GOAL',name,scorer.name,assist.name if assist else None,f'⚽ {scorer.name} забивает!',{},scorer.id,assist.id if assist else None))
        _goal_sequence(events,scorer,assist,name,minute,positions,visual_rng)
        if assist:
            events.append(MatchEvent(minute,'ASSIST',name,assist.name,scorer.name,f'🅰️ Передача {assist.name}.',{},assist.id,scorer.id))

    hs=sum(1 for name,_ in goals if name=='HOME')
    as_=sum(1 for name,_ in goals if name=='AWAY')
    home_total,home_on_target=_shots_from_xg(hfx*.48,hs,rng)
    away_total,away_on_target=_shots_from_xg(afx*.48,as_,rng)
    _add_open_play_events(events,home,away,ht,at,home_board,away_board,seed*17+3,3,44,hfx,afx)
    _add_shot_events(events,home,away,ht,at,home_board,away_board,seed*17+4,6,44,home_total,away_total,home_on_target,away_on_target)
    _add_corner_events(events,home,away,_corner_count(ht,'first'),_corner_count(at,'first'),seed*17+6,7,44)
    _add_foul_events(events,home,away,seed*17+7,4,44,half=half)
    for _ in range(rng.randint(0,2)):
        team=home if rng.random()<.5 else away; name='HOME' if team is home else 'AWAY'; candidates=[x for x in team if x.position!='GK'] or list(team)
        if not candidates: continue
        p=rng.choice(candidates)
        events.append(MatchEvent(rng.randint(8,44),'YELLOW',name,p.name,text=f'🟨 {p.name} получает жёлтую.'))
        _ai_comment(events[-1], 'HOME', 'AWAY', hs, as_)
    if rng.random()<0.025:
        team=home if rng.random()<.5 else away; name='HOME' if team is home else 'AWAY'
        candidates=[x for x in team if x.position!='GK'] or list(team)
        if candidates:
            p=rng.choice(candidates)
            events.append(MatchEvent(rng.randint(20,44),'RED',name,p.name,text=f'🟥 {p.name} удалён.'))
            _ai_comment(events[-1], 'HOME', 'AWAY', hs, as_)
    if rng.random()<0.035:
        team=home if rng.random()<.5 else away; name='HOME' if team is home else 'AWAY'
        candidates=[x for x in team if x.position!='GK'] or list(team)
        if candidates:
            p=rng.choice(candidates)
            events.append(MatchEvent(rng.randint(10,44),'INJURY',name,p.name,text=f'🩹 {p.name} получил травму.'))
    events.sort(key=lambda e:(e.minute,_EVENT_ORDER.get(e.type,99)))
    return hs,as_,events

def simulate_first_half(home, away, ht, at, seed, home_counter=None, away_counter=None, home_board=None, away_board=None, weather=None, brilliance_excluded_ids=None):
    hs,as_,events=_half_result(home,away,ht,at,seed,1,home_counter,away_counter,home_board,away_board,weather,brilliance_excluded_ids)
    return hs,as_,events

def _ai_possession(events, home_tactics, away_tactics):
    """Deterministic possession proxy from live actions + current tactics."""
    home_actions=sum(1 for e in events if e.team=='HOME' and e.type in {'PASS','CARRY','INTERCEPTION'})
    away_actions=sum(1 for e in events if e.team=='AWAY' and e.type in {'PASS','CARRY','INTERCEPTION'})
    tactical=(home_tactics.tempo-away_tactics.tempo)*0.06 + (home_tactics.pressing-away_tactics.pressing)*0.035
    flow=(home_actions-away_actions)*0.35
    home=max(35.0,min(65.0,50.0+tactical+flow))
    return home,100.0-home


def _ai_shots_on_target(events, team):
    return sum(1 for e in events if e.team==team and e.type=='SHOT' and str((e.metadata or {}).get('result','')).upper() in {'GOAL','SAVED'})


def _ai_fatigue_average(fatigue, players):
    values=[float(fatigue.get(int(p.id),0.0)) for p in players if getattr(p,'id',None) is not None]
    return sum(values)/len(values) if values else 0.0


def _ai_log_event(events, minute, team, old_tactics, new_tactics):
    if new_tactics == old_tactics:
        return
    if old_tactics.style != new_tactics.style:
        text=f'🤖 AI-тренер меняет тактику: {old_tactics.style.value} -> {new_tactics.style.value} ({minute}-я минута)'
    elif old_tactics.formation != new_tactics.formation:
        text=f'🤖 AI-тренер меняет формацию: {old_tactics.formation} -> {new_tactics.formation} ({minute}-я минута)'
    else:
        text=f'🤖 AI-тренер корректирует слайдеры {new_tactics.style.value} ({minute}-я минута)'
    events.append(MatchEvent(
        minute, 'TACTICAL_CHANGE', team, None, None, text,
        {'visual_type':'TACTICAL_CHANGE', 'old_style':old_tactics.style.value,
         'new_style':new_tactics.style.value, 'old_formation':old_tactics.formation,
         'new_formation':new_tactics.formation,
         'old_sliders':{'tempo':old_tactics.tempo,'pressing':old_tactics.pressing,'width':old_tactics.width,'defensive_line':old_tactics.defensive_line},
         'new_sliders':{'tempo':new_tactics.tempo,'pressing':new_tactics.pressing,'width':new_tactics.width,'defensive_line':new_tactics.defensive_line,'aggression':new_tactics.aggression}},
    ))


def simulate_second_half(home, away, ht, at, seed, substitutions=None, home_counter=None, away_counter=None,
                         home_board=None, away_board=None, initial_home_score=0, initial_away_score=0,
                         home_is_bot=False, away_is_bot=False, weather=None, brilliance_excluded_ids=None,
                         ai_commentary=True, return_injury_time=False, allow_extra_time=False, return_extra_time=False):
    """Deterministic minute-by-minute second half with optional bot AI coaching.

    Bot tactics are re-evaluated every five minutes from minute 60 onward.
    The second-half goal budget is also generated in five-minute chunks, so an
    AI tactical change changes the xG used for subsequent goal rolls rather than
    being cosmetic. Existing incidents/substitutions and deterministic seeds
    remain intact.
    """
    substitutions=sorted(substitutions or [], key=lambda x:(x[0],x[1]))
    rng=Random(seed); weather=weather or generate_weather(Random(seed*1009+1)); brilliance_excluded_ids=set(brilliance_excluded_ids or ()); visual_rng=Random(seed*31+46); events=[]; hs=as_=0
    current_weather=weather
    home_now=list(home); away_now=list(away)
    home_tactics=ht; away_tactics=at
    home_board_now=home_board or TacticalBoard(home_tactics.formation)
    away_board_now=away_board or TacticalBoard(away_tactics.formation)
    home_fatigue=_initial_fatigue(home_now,home_tactics,45,weather); away_fatigue=_initial_fatigue(away_now,away_tactics,45,weather)
    home_morale={int(p.id):50.0 for p in home_now if getattr(p,'id',None) is not None}
    away_morale={int(p.id):50.0 for p in away_now if getattr(p,'id',None) is not None}
    sub_by_minute={}
    for item in substitutions:
        sub_by_minute.setdefault(int(item[0]),[]).append(item)
    brilliance_rng=Random(seed*1013+2)
    for team,side in ((home_now,'HOME'),(away_now,'AWAY')):
        for player in team:
            if getattr(player,'id',None) in brilliance_excluded_ids:
                continue
            if roll_brilliance(player,brilliance_rng):
                bonus=brilliance_xg_bonus(brilliance_rng)
                # Keep the bonus tied to the live segment where the moment occurs.
                minute=brilliance_rng.randint(46,88)
                events.append(MatchEvent(minute,'MOMENT_OF_BRILLIANCE',side,player.name,None,f'✨ Гениальный момент: {player.name} создает шанс!',{'xg_bonus':bonus,'weather':current_weather},player.id,None))
                logger.info('✨ Гениальный момент: %s создает шанс!',player.name)
                # The segment resolver reads this event as a local xG boost.
                break
    incidents=[]
    for typ, chance, lo, hi in (('RED',.02,55,89),('INJURY',.035,50,88)):
        if rng.random() < chance:
            team_name='HOME' if rng.random()<.5 else 'AWAY'
            pool=home_now if team_name=='HOME' else away_now
            candidates=[p for p in pool if p.position!='GK'] or list(pool)
            if candidates:
                p=rng.choice(candidates); incidents.append((rng.randint(lo,hi),typ,team_name,p.id))
    incidents.sort(key=lambda x:(x[0], _EVENT_ORDER.get(x[1],99), x[3]))
    incidents_by_minute={}
    for item in incidents: incidents_by_minute.setdefault(item[0],[]).append(item)

    # Each 5-minute segment gets a fresh xG calculation from the current
    # tactics/context. This is the integration point that makes AI changes
    # materially affect goals while retaining the live replay architecture.
    second_half_xg=[0.0,0.0]
    goals_remaining=max(0,5-int(initial_home_score or 0)-int(initial_away_score or 0))
    # A separate deterministic stream avoids changing the established event RNG.
    injury_time=Random(int(seed)*7919+90).randint(1,5)
    for minute in range(46,91+injury_time):
        for _,off_id,on_player,team in sub_by_minute.get(minute,[]):
            pool=home_now if team=='HOME' else away_now
            idx=next((i for i,p in enumerate(pool) if p.id==off_id),None)
            if idx is not None and on_player.id not in {p.id for p in pool}:
                pool[idx]=on_player
                fatigue=home_fatigue if team=='HOME' else away_fatigue
                morale=home_morale if team=='HOME' else away_morale
                fatigue[int(on_player.id)]=0.0; morale[int(on_player.id)]=50.0
                events.append(MatchEvent(minute,'SUBSTITUTION',team,on_player.name,None,f'🔄 {on_player.name} выходит вместо #{off_id}.',{'visual_type':'SUBSTITUTION'}))
        for _,typ,team_name,pid in incidents_by_minute.get(minute,[]):
            pool=home_now if team_name=='HOME' else away_now
            idx=next((i for i,p in enumerate(pool) if p.id==pid),None)
            if idx is None: continue
            player=pool[idx]
            pool.pop(idx)
            event_text='🟥' if typ=='RED' else '🩹'
            event_text += f' {player.name} ' + ('удалён.' if typ=='RED' else 'получил травму.')
            metadata={'visual_type':'CARD','card':'RED'} if typ=='RED' else {'visual_type':'INJURY'}
            events.append(MatchEvent(minute,typ,team_name,player.name,None,event_text,metadata,player.id,None))
            if ai_commentary:
                _ai_comment(events[-1], 'HOME', 'AWAY', initial_home_score + hs, initial_away_score + as_)

        _advance_fatigue(home_fatigue,home_now,home_tactics,current_weather)
        _advance_fatigue(away_fatigue,away_now,away_tactics,current_weather)

        if minute==60 and rng.random()<0.08:
            choices=['CLEAR','RAIN','SNOW','WIND','HEAT']
            choices=[x for x in choices if x!=current_weather]
            new_weather=rng.choice(choices)
            current_weather=new_weather
            events.append(MatchEvent(minute,'WEATHER','HOME',None,None,f'🌦️ Погода изменилась: {weather_label(new_weather)}',{'weather':new_weather,'previous_weather':weather},None,None))
        if minute % 5 == 0 and minute >= 60:
            hp,ap=_ai_possession(events,home_tactics,away_tactics)
            hsot=_ai_shots_on_target(events,'HOME'); asot=_ai_shots_on_target(events,'AWAY')
            hctx={'minute':minute,'home_score':int(initial_home_score)+hs,'away_score':int(initial_away_score)+as_,
                  'possession':hp,'shots_on_target_home':hsot,'shots_on_target_away':asot,
                  'fatigue_home':_ai_fatigue_average(home_fatigue,home_now),'fatigue_away':_ai_fatigue_average(away_fatigue,away_now),'is_home':True,'weather':current_weather}
            actx={**hctx,'possession':ap,'is_home':False}
            if home_is_bot:
                new=adjust_tactics(home_tactics,hctx)
                _ai_log_event(events,minute,'HOME',home_tactics,new)
                if new.formation != home_tactics.formation:
                    home_board_now=TacticalBoard(new.formation)
                home_tactics=new
            if away_is_bot:
                new=adjust_tactics(away_tactics,actx)
                _ai_log_event(events,minute,'AWAY',away_tactics,new)
                if new.formation != away_tactics.formation:
                    away_board_now=TacticalBoard(new.formation)
                away_tactics=new

        # Generate one five-minute goal segment using current tactics.  At the
        # first/last partial segment use the actual minute span.
        if minute % 5 == 0 or minute == 90:
            segment_end=minute
            segment_start=max(46,minute-4)
            span=segment_end-segment_start+1
            context={'minute':minute,'home_score':int(initial_home_score)+hs,'away_score':int(initial_away_score)+as_,
                     'home':home_now,'away':away_now,'home_fatigue':home_fatigue,'away_fatigue':away_fatigue,
                     'home_morale':home_morale,'away_morale':away_morale,'weather':current_weather}
            hfx,afx=expected_goals(home_now,away_now,home_tactics,away_tactics,home_board=home_board_now,away_board=away_board_now,
                                    home_counter=home_counter,away_counter=away_counter,match_context=context)
            # expected_goals is a 90-minute calibrated value; the second half
            # contribution is .52 and the segment receives its proportional share.
            segment_home=max(0.0,hfx*.52*span/45.0); segment_away=max(0.0,afx*.52*span/45.0)
            for brilliance_event in events:
                if brilliance_event.type=='MOMENT_OF_BRILLIANCE' and segment_start <= brilliance_event.minute <= segment_end:
                    if brilliance_event.team=='HOME': segment_home += float((brilliance_event.metadata or {}).get('xg_bonus',0.0))
                    else: segment_away += float((brilliance_event.metadata or {}).get('xg_bonus',0.0))
            second_half_xg[0]+=segment_home; second_half_xg[1]+=segment_away
            # Keep the adaptive second-half xG visible to the live persistence
            # layer without changing the public return signature.
            for ai_event in reversed(events):
                if ai_event.type=='TACTICAL_CHANGE' and ai_event.team=='HOME':
                    ai_event.metadata['second_half_xg']=round(second_half_xg[0],2); break
            for ai_event in reversed(events):
                if ai_event.type=='TACTICAL_CHANGE' and ai_event.team=='AWAY':
                    ai_event.metadata['second_half_xg']=round(second_half_xg[1],2); break
            if goals_remaining>0:
                home_lambda=segment_home*conversion_modifier(home_tactics)
                away_lambda=segment_away*conversion_modifier(away_tactics)
                total_lambda=home_lambda+away_lambda
                if total_lambda>0:
                    segment_goals=min(goals_remaining,poisson(rng,total_lambda))
                    for _ in range(segment_goals):
                        if goals_remaining<=0: break
                        name='HOME' if rng.random() < home_lambda/max(total_lambda,1e-9) else 'AWAY'
                        pool=home_now if name=='HOME' else away_now
                        scorer=pick_scorer(rng,pool)
                        if scorer is None: continue
                        assist=pick_assist(rng,pool,scorer)
                        events.append(MatchEvent(minute,'GOAL',name,scorer.name,assist.name if assist else None,f'⚽ {scorer.name} забивает!',{},scorer.id,assist.id if assist else None))
                        if ai_commentary:
                            _ai_comment(events[-1], 'HOME', 'AWAY', initial_home_score + hs + (1 if name=='HOME' else 0), initial_away_score + as_ + (1 if name=='AWAY' else 0))
                        _goal_sequence(events,scorer,assist,name,minute,_player_map(pool,home_board_now if name=='HOME' else away_board_now,name),visual_rng)
                        if assist:
                            events.append(MatchEvent(minute,'ASSIST',name,assist.name,scorer.name,f'🅰️ Передача {assist.name}.',{},assist.id,scorer.id))
                        if name=='HOME': hs+=1
                        else: as_+=1
                        goals_remaining-=1

        if minute % 5 == 0 and minute <= 90:
            _add_open_play_events(events,home_now,away_now,home_tactics,away_tactics,home_board_now,away_board_now,seed*19+minute,minute,min(90,minute+4),
                                  max(.1,second_half_xg[0]),max(.1,second_half_xg[1]),count=1)
        elif minute > 90:
            added=minute-90
            # A compact added-time sequence: every added minute can create a shot/foul,
            # and a low-probability goal. It is deterministic with the match RNG.
            team_name='HOME' if rng.random()<0.5 else 'AWAY'
            pool=home_now if team_name=='HOME' else away_now
            candidates=[p for p in pool if getattr(p,'position',None)!='GK'] or list(pool)
            player=rng.choice(candidates) if candidates else None
            if player and rng.random()<0.035 and goals_remaining>0:
                assist=pick_assist(rng,pool,player)
                added_goal=MatchEvent(minute,'GOAL',team_name,player.name,assist.name if assist else None,
                    f'⚽ ГОЛ! {player.name} забивает в добавленное время.',
                    {'is_added_time':True,'added_minute':added},player.id,assist.id if assist else None)
                added_goal.text=f'90+{added}′ {added_goal.text}'
                events.append(added_goal)
                _goal_sequence(events,player,assist,team_name,minute,_player_map(pool,home_board_now if team_name=='HOME' else away_board_now,team_name),visual_rng)
                if assist:
                    added_assist=MatchEvent(minute,'ASSIST',team_name,assist.name,player.name,f'🅰️ Передача {assist.name}.',{'is_added_time':True,'added_minute':added},assist.id,player.id)
                    added_assist.text=f'90+{added}′ {added_assist.text}'
                    events.append(added_assist)
                if team_name=='HOME': hs+=1
                else: as_+=1
                goals_remaining-=1
            elif player and rng.random()<0.55:
                added_shot=MatchEvent(minute,'SHOT',team_name,player.name,None,
                    f'{player.name} пробует решить исход матча ударом!',
                    {'is_added_time':True,'added_minute':added,'result':'SAVED' if rng.random()<0.35 else 'BLOCKED'},player.id,None)
                added_shot.text=f'90+{added}′ {added_shot.text}'
                events.append(added_shot)
            elif player:
                added_foul=MatchEvent(minute,'FOUL',team_name,player.name,None,
                    f'Фол в концовке: {player.name}.',
                    {'is_added_time':True,'added_minute':added},player.id,None)
                added_foul.text=f'90+{added}′ {added_foul.text}'
                events.append(added_foul)

    # Knockout cup matches may proceed to two 15-minute extra-time periods.
    # Internal event minutes remain monotonic after regulation added time; UI
    # uses metadata.display_minute to show the conventional 91'..120' clock.
    regulation_home=int(initial_home_score)+hs
    regulation_away=int(initial_away_score)+as_
    extra_time_played=bool(allow_extra_time and regulation_home==regulation_away)
    if extra_time_played:
        et_rng=Random(int(seed)*104729+120)
        for et_minute in range(1,31):
            minute=90+injury_time+et_minute
            # One low-frequency scoring roll per side per extra-time minute.
            for team_name,pool in (('HOME',home_now),('AWAY',away_now)):
                candidates=[p for p in pool if getattr(p,'position',None)!='GK'] or list(pool)
                if not candidates or et_rng.random() >= 0.018:
                    continue
                scorer=et_rng.choice(candidates)
                assist=pick_assist(et_rng,pool,scorer)
                event=MatchEvent(minute,'GOAL',team_name,scorer.name,assist.name if assist else None,
                    f'⚽ Гол в дополнительное время: {scorer.name}!',
                    {'extra_time':True,'display_minute':90+et_minute,'extra_time_minute':et_minute},
                    getattr(scorer,'id',None),getattr(assist,'id',None) if assist else None)
                events.append(event)
                if team_name=='HOME': hs+=1
                else: as_+=1
                if assist:
                    events.append(MatchEvent(minute,'ASSIST',team_name,assist.name,scorer.name,
                        f'🅰️ Передача {assist.name}.',
                        {'extra_time':True,'display_minute':90+et_minute,'extra_time_minute':et_minute},
                        getattr(assist,'id',None),getattr(scorer,'id',None)))
            if et_rng.random() < 0.42:
                team_name='HOME' if et_rng.random()<0.5 else 'AWAY'
                pool=home_now if team_name=='HOME' else away_now
                candidates=[p for p in pool if getattr(p,'position',None)!='GK'] or list(pool)
                if candidates:
                    player=et_rng.choice(candidates)
                    events.append(MatchEvent(minute,'SHOT',team_name,player.name,None,
                        f'{player.name} бьёт в дополнительное время.',
                        {'extra_time':True,'display_minute':90+et_minute,'extra_time_minute':et_minute,'result':'SAVED'},
                        getattr(player,'id',None),None))
        events.sort(key=lambda e:(e.minute,_EVENT_ORDER.get(e.type,99)))

    final_minute=90+injury_time+(30 if extra_time_played else 0)
    # Final shot/corner/foul aggregation uses the final adaptive state.
    final_context={'minute':final_minute,'home_score':int(initial_home_score)+hs,'away_score':int(initial_away_score)+as_,'home':home_now,'away':away_now,
                   'home_fatigue':home_fatigue,'away_fatigue':away_fatigue,'home_morale':home_morale,'away_morale':away_morale,'weather':current_weather}
    hx,ax=expected_goals(home_now,away_now,home_tactics,away_tactics,home_board=home_board_now,away_board=away_board_now,home_counter=home_counter,away_counter=away_counter,match_context=final_context)
    home_total,home_on_target=_shots_from_xg(max(second_half_xg[0],hx*.52),hs,rng); away_total,away_on_target=_shots_from_xg(max(second_half_xg[1],ax*.52),as_,rng)
    _add_shot_events(events,home_now,away_now,home_tactics,away_tactics,home_board_now,away_board_now,seed*29+1,46,90,home_total,away_total,home_on_target,away_on_target)
    _add_corner_events(events,home_now,away_now,_corner_count(home_tactics,'second'),_corner_count(away_tactics,'second'),seed*29+2,46,90)
    _add_foul_events(events,home_now,away_now,seed*29+3,46,90,half=2)
    for _ in range(rng.randint(0,3)):
        team=home_now if rng.random()<.5 else away_now; candidates=[x for x in team if x.position!='GK'] or list(team)
        if not candidates: continue
        p=rng.choice(candidates); name='HOME' if team is home_now else 'AWAY'
        events.append(MatchEvent(rng.randint(46,89),'YELLOW',name,p.name,text=f'🟨 {p.name} получает жёлтую.'))
        if ai_commentary:
            _ai_comment(events[-1], 'HOME', 'AWAY', initial_home_score + hs, initial_away_score + as_)
    events.sort(key=lambda e:(e.minute,_EVENT_ORDER.get(e.type,99)))
    if return_extra_time:
        return hs,as_,events,injury_time,extra_time_played
    if return_injury_time:
        return hs,as_,events,injury_time
    return hs,as_,events
