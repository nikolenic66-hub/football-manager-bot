"""Bounded, condition-driven player talents for Iteration 4.

Talents are virtual traits inferred from the existing player attributes. No
schema change is required. The engine applies each talent at most once per
team; LEADER is a deliberate two-player synergy.
"""

from app.game.tactics import Style


TALENT_LABELS_RU = {
    'LEADER':'Лидер', 'AERIAL_DUELIST':'Воздушный дуэлянт', 'DRIBBLER':'Дриблёр',
    'PLAYMAKER':'Плеймейкер', 'COUNTERATTACKER':'Контратакующий', 'COUNTER_RUNNER':'Контратакующий бегунок',
    'PRESSER':'Прессингующий', 'COMPOSED_FINISHER':'Хладнокровный', 'PRESS_RESISTANT':'Устойчивый к прессингу',
    'CROSSER':'Кроссер', 'AERIAL_COMMANDER':'Воздушный командир', 'BALL_WINNER':'Отборщик',
    'TEMPO_CONTROLLER':'Контролёр темпа', 'ANCHOR':'Опорник', 'PACE_RUNNER':'Спринтер',
    'BOX_TO_BOX':'Бокс-ту-бокс', 'BALL_PLAYING_DEFENDER':'Центральный защитник, начинающий атаки',
    'SWEEPER_KEEPER':'Вратарь-либеро', 'SHOT_STOPPER':'Вратарь-отбивающий', 'DISTRIBUTOR':'Вратарь-распасовщик',
    'STOPPER':'Стоппер', 'AERIAL_BEAST':'Воздушный защитник', 'RECOVERY_DEFENDER':'Страхующий защитник',
    'OVERLAP_RUNNER':'Подключающийся фулбек', 'INVERTED_FULLBACK':'Инвертированный фулбек',
    'ONE_V_ONE_DEFENDER':'Защитник один в один', 'DEEP_PLAYMAKER':'Глубинный плеймейкер',
    'DESTROYER':'Разрушитель', 'CARRIER':'Продвигающий мяч', 'THROUGH_BALLER':'Мастер проникающих передач',
    'SECOND_STRIKER':'Второй нападающий', 'PRESSING_10':'Прессингующий атакующий полузащитник',
    'CREATIVE_ENGINE':'Креативный двигатель', 'INSIDE_FORWARD':'Инсайд', 'WINGER':'Вингер',
    'CREATIVE_DRIBBLER':'Креативный дриблёр', 'POACHER':'Наконечник', 'TARGET_MAN':'Таран',
    'PRESSING_FORWARD':'Прессингующий нападающий', 'FALSE_NINE':'Ложная девятка', 'COMPLETE_FORWARD':'Универсальный нападающий',
}


def talent_label(talent_id):
    return TALENT_LABELS_RU.get(str(talent_id), str(talent_id).replace('_',' ').title())


def _tactic_style(style, value):
    try:
        return style == value or getattr(style, 'value', style) == getattr(value, 'value', value)
    except Exception:
        return False


def _ctx_score_diff(context):
    if not context:
        return 0
    value=context.get('score_diff')
    return int(value or 0)


def _always(tactics, opponent_tactics, match_context):
    return True


def _opponent_high_line_or_press(tactics, opponent_tactics, match_context):
    return opponent_tactics is not None and (opponent_tactics.defensive_line>65 or opponent_tactics.pressing>65)


def _own_width(tactics, opponent_tactics, match_context):
    return tactics is not None and tactics.width>65


def _possession(tactics, opponent_tactics, match_context):
    return tactics is not None and _tactic_style(tactics.style, Style.POSSESSION)


def _counter(tactics, opponent_tactics, match_context):
    return tactics is not None and _tactic_style(tactics.style, Style.COUNTER)


def _pressing(tactics, opponent_tactics, match_context):
    return tactics is not None and tactics.pressing>65


def _trailing(tactics, opponent_tactics, match_context):
    return _ctx_score_diff(match_context)<0


def _opponent_press(tactics, opponent_tactics, match_context):
    return opponent_tactics is not None and opponent_tactics.pressing>65


def _opponent_low_line(tactics, opponent_tactics, match_context):
    return opponent_tactics is not None and opponent_tactics.defensive_line<40


def _aggressive(tactics, opponent_tactics, match_context):
    return tactics is not None and tactics.aggression>65


def _tempo(tactics, opponent_tactics, match_context):
    return tactics is not None and tactics.tempo>65


def _defensive_line(tactics, opponent_tactics, match_context):
    return tactics is not None and tactics.defensive_line>65


# Positions are only used to decide whether a virtual trait is natural to the
# player. An off-position trait keeps 65% of its already bounded effect.
TALENT_POSITIONS={
    'LEADER': {'CB','DM','CM','AM','ST'},
    'AERIAL_DUELIST': {'GK','CB','ST'},
    'DRIBBLER': {'LW','RW','AM','ST'},
    'PLAYMAKER': {'DM','CM','AM'},
    'COUNTERATTACKER': {'LW','RW','AM','ST'},
    'COUNTER_RUNNER': {'LW','RW','AM','ST'},
    'PRESSER': {'DM','CM','AM','LW','RW','ST'},
    'COMPOSED_FINISHER': {'AM','ST','LW','RW'},
    'PRESS_RESISTANT': {'DM','CM','AM'},
    'CROSSER': {'LB','RB','LW','RW'},
    'AERIAL_COMMANDER': {'GK','CB','DM'},
    'BALL_WINNER': {'CB','DM','CM'},
    'TEMPO_CONTROLLER': {'DM','CM','AM'},
    'ANCHOR': {'CB','DM'},
    'PACE_RUNNER': {'LB','RB','LW','RW','ST'},
    'BOX_TO_BOX': {'CM','DM','AM'},
}

TALENTS={
    'LEADER': {
        'name':'Лидер',
        'description':'2+ лидера на поле усиливают контроль центра.',
        'trigger':_always,'phase':'mid','effect':0.04,
    },
    'AERIAL_DUELIST': {
        'name':'Воздушный дуэлянт',
        'description':'Высокая линия или прессинг соперника повышают защитную устойчивость.',
        'trigger':_opponent_high_line_or_press,'phase':'def','effect':0.04,
    },
    'DRIBBLER': {
        'name':'Дриблёр',
        'description':'Широкая игра команды усиливает атаку.',
        'trigger':_own_width,'phase':'att','effect':0.04,
    },
    'PLAYMAKER': {
        'name':'Плеймейкер',
        'description':'Стиль POSSESSION усиливает контроль и продвижение мяча.',
        'trigger':_possession,'phase':'mid','effect':0.04,
    },
    'COUNTERATTACKER': {
        'name':'Контратакующий',
        'description':'Стиль COUNTER усиливает атакующую фазу.',
        'trigger':_counter,'phase':'att','effect':0.04,
    },
    'COUNTER_RUNNER': {
        'name':'Контратакующий бегунок',
        'description':'Стиль COUNTER усиливает атакующую фазу.',
        'trigger':_counter,'phase':'att','effect':0.04,
    },
    'PRESSER': {
        'name':'Прессингующий',
        'description':'Высокий прессинг усиливает середину поля.',
        'trigger':_pressing,'phase':'mid','effect':0.035,
    },
    'COMPOSED_FINISHER': {
        'name':'Хладнокровный',
        'description':'При отставании в счёте усиливает атаку.',
        'trigger':_trailing,'phase':'att','effect':0.04,
    },
    'PRESS_RESISTANT': {
        'name':'Устойчивый к прессингу',
        'description':'Высокий прессинг соперника усиливает контроль мяча.',
        'trigger':_opponent_press,'phase':'mid','effect':0.035,
    },
    'CROSSER': {
        'name':'Кроссер',
        'description':'Низкая линия соперника открывает фланговые варианты.',
        'trigger':_opponent_low_line,'phase':'att','effect':0.03,
    },
    'AERIAL_COMMANDER': {
        'name':'Воздушный командир',
        'description':'Высокая линия своей команды усиливает оборонительную координацию.',
        'trigger':_defensive_line,'phase':'def','effect':0.04,
    },
    'BALL_WINNER': {
        'name':'Отборщик',
        'description':'Высокий прессинг усиливает оборонительную фазу.',
        'trigger':_pressing,'phase':'def','effect':0.04,
    },
    'TEMPO_CONTROLLER': {
        'name':'Контролёр темпа',
        'description':'Высокий темп требует сильного контроля центра.',
        'trigger':_tempo,'phase':'mid','effect':0.03,
    },
    'ANCHOR': {
        'name':'Опорник',
        'description':'Высокая линия соперника усиливает защитную опору.',
        'trigger':_opponent_high_line_or_press,'phase':'def','effect':0.035,
    },
    'PACE_RUNNER': {
        'name':'Спринтер',
        'description':'Широкая команда создаёт пространство для рывков.',
        'trigger':_own_width,'phase':'att','effect':0.035,
    },
    'BOX_TO_BOX': {
        'name':'Box-to-box',
        'description':'Высокий прессинг усиливает вклад игрока в центр.',
        'trigger':_pressing,'phase':'mid','effect':0.03,
    },
}


def _stats(player):
    return {key:float(getattr(player,key,0) or 0) for key in
            ('pace','shooting','passing','dribbling','defending','physical','stamina','mental')}


def _virtual_talents(player):
    """Infer a small set of traits directly from existing player attributes."""
    s=_stats(player); pos=getattr(player,'position',None); result=[]
    if s['mental']>=82 and s['passing']>=78 and pos in TALENT_POSITIONS['LEADER']:
        result.append('LEADER')
    if (s['physical']>=82 and s['defending']>=78 and pos in {'GK','CB','DM','ST'}) or (s['physical']>=88 and pos=='CB'):
        result.append('AERIAL_DUELIST')
    if s['dribbling']>=84 and s['pace']>=78 and pos in TALENT_POSITIONS['DRIBBLER']:
        result.append('DRIBBLER')
    if s['passing']>=85 and s['mental']>=80 and pos in TALENT_POSITIONS['PLAYMAKER']:
        result.append('PLAYMAKER')
    if s['pace']>=84 and s['dribbling']>=78 and pos in TALENT_POSITIONS['COUNTERATTACKER']:
        result.append('COUNTERATTACKER')
    if s['stamina']>=82 and s['mental']>=75 and pos in TALENT_POSITIONS['PRESSER']:
        result.append('PRESSER')
    if s['shooting']>=86 and s['mental']>=82 and pos in TALENT_POSITIONS['COMPOSED_FINISHER']:
        result.append('COMPOSED_FINISHER')
    if s['passing']>=82 and s['mental']>=82 and pos in TALENT_POSITIONS['PRESS_RESISTANT']:
        result.append('PRESS_RESISTANT')
    if s['passing']>=82 and s['dribbling']>=75 and pos in TALENT_POSITIONS['CROSSER']:
        result.append('CROSSER')
    if s['mental']>=84 and s['defending']>=78 and pos in TALENT_POSITIONS['AERIAL_COMMANDER']:
        result.append('AERIAL_COMMANDER')
    if s['defending']>=84 and s['physical']>=80 and pos in TALENT_POSITIONS['BALL_WINNER']:
        result.append('BALL_WINNER')
    if s['passing']>=84 and s['mental']>=84 and pos in TALENT_POSITIONS['TEMPO_CONTROLLER']:
        result.append('TEMPO_CONTROLLER')
    if s['defending']>=86 and s['mental']>=78 and pos in TALENT_POSITIONS['ANCHOR']:
        result.append('ANCHOR')
    if s['pace']>=88 and s['stamina']>=80 and pos in TALENT_POSITIONS['PACE_RUNNER']:
        result.append('PACE_RUNNER')
    if s['stamina']>=84 and s['physical']>=80 and pos in TALENT_POSITIONS['BOX_TO_BOX']:
        result.append('BOX_TO_BOX')
    return tuple(result)


def _phase_tuple():
    return [1.0,1.0,1.0]


def talent_effects(player, tactics, opponent_tactics, match_context=None):
    """Return one player's bounded talent contribution by phase."""
    context=dict(match_context or {})
    inferred=_virtual_talents(player)
    # Compatibility: explicit model talents can supply an existing trait when
    # present, without changing the schema. Virtual inference remains primary.
    explicit=getattr(player,'talents',()) or ()
    talent_ids=list(inferred)
    for talent_id in explicit:
        if talent_id in TALENTS and talent_id not in talent_ids:
            talent_ids.append(talent_id)
    out=_phase_tuple()
    counts=context.get('_talent_counts') or {}
    only_talent=context.get('_only_talent')
    for talent_id in talent_ids:
        if only_talent and talent_id!=only_talent:
            continue
        spec=TALENTS.get(talent_id)
        if not spec:
            continue
        if talent_id=='LEADER' and int(counts.get('LEADER',0))<2:
            continue
        if not spec['trigger'](tactics,opponent_tactics,context):
            continue
        effect=float(spec['effect'])
        if getattr(player,'position',None) not in TALENT_POSITIONS.get(talent_id,set()):
            effect*=0.65
        index={'att':0,'mid':1,'def':2}[spec['phase']]
        out[index]+=effect
    return tuple(out)


def apply_talents(slots, tactics, opponent_tactics, match_context=None):
    """Aggregate starting-XI talents once per talent, capped to 0.90..1.10."""
    players=list(slots or [])
    context=dict(match_context or {})
    talent_sets=[set(_virtual_talents(player)) | {t for t in (getattr(player,'talents',()) or ()) if t in TALENTS} for player in players]
    counts={talent_id:sum(talent_id in ids for ids in talent_sets) for talent_id in TALENTS}
    context['_talent_counts']=counts
    totals=[1.0,1.0,1.0]
    seen=set()
    for player,ids in zip(players,talent_sets):
        for talent_id in ids:
            if talent_id in seen:
                continue
            seen.add(talent_id)
            one=talent_effects(player,tactics,opponent_tactics,{**context,'_only_talent':talent_id})
            spec=TALENTS.get(talent_id)
            if not spec:
                continue
            index={'att':0,'mid':1,'def':2}[spec['phase']]
            totals[index]=totals[index]+(one[index]-1.0)
    return tuple(max(0.90,min(1.10,value)) for value in totals)

