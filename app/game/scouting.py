from collections import Counter
from dataclasses import dataclass

from sqlalchemy import Column, Integer, MetaData, String, Table, and_, bindparam, not_, select, func


@dataclass(frozen=True)
class ScoutingReport:
    opponent_id: int
    opponent_name: str
    sample_matches: int
    typical_formation: str
    typical_style: str
    avg_goals_for: float
    avg_goals_against: float
    key_players: tuple[str, ...]
    tendencies: tuple[str, ...]
    counter_recommendations: tuple[str, ...]


_PLAYER_COLUMNS = (
    'id', 'first_name', 'last_name', 'nationality', 'age', 'position', 'pace',
    'shooting', 'passing', 'dribbling', 'defending', 'physical', 'stamina',
    'mental', 'market_value', 'salary', 'potential', 'preferred_foot',
)


def _players_table():
    metadata = MetaData()
    return Table('players', metadata, *[Column(name, String if name in {'first_name','last_name','nationality','position','preferred_foot'} else Integer) for name in _PLAYER_COLUMNS])


def _range_condition(column, spec):
    if spec is None:
        return None
    if isinstance(spec, dict):
        lo, hi = spec.get('min'), spec.get('max')
    elif isinstance(spec, (tuple, list)):
        lo = spec[0] if len(spec) > 0 else None
        hi = spec[1] if len(spec) > 1 else None
    else:
        lo, hi = None, spec
    clauses = []
    if lo is not None:
        clauses.append(column >= bindparam(f'{column.name}_min', lo))
    if hi is not None:
        clauses.append(column <= bindparam(f'{column.name}_max', hi))
    return and_(*clauses) if clauses else None


def build_scout_query(filters=None):
    """Build a deterministic SQLAlchemy Select for the player scout.

    Supported filters: position (str/list), age, potential, value, wage,
    nationality, transfer_status, preferred_foot and numeric attributes.
    The caller may add pagination with limit/offset to this Select.
    """
    filters = filters or {}
    p = _players_table()
    conditions = []

    positions = filters.get('position')
    if positions:
        if isinstance(positions, str):
            positions = [positions]
        conditions.append(p.c.position.in_(list(positions)))

    for key, column_name in (
        ('age', 'age'), ('potential', 'potential'), ('value', 'market_value'), ('wage', 'salary')
    ):
        condition = _range_condition(getattr(p.c, column_name), filters.get(key))
        if condition is not None:
            conditions.append(condition)

    if filters.get('nationality'):
        conditions.append(p.c.nationality == bindparam('nationality', str(filters['nationality']).upper()[:3]))
    if filters.get('preferred_foot'):
        conditions.append(p.c.preferred_foot == bindparam('preferred_foot', filters['preferred_foot']))

    transfer_status = filters.get('transfer_status')
    active_listing = "EXISTS (SELECT 1 FROM transfer_listings tl WHERE tl.player_id=players.id AND tl.status='LISTED')"
    owned = "EXISTS (SELECT 1 FROM club_players cp WHERE cp.player_id=players.id)"
    # Raw correlated predicates keep this builder independent from an ORM
    # mapping, while the returned object is a normal SQLAlchemy Select.
    from sqlalchemy import text as sql_text
    if transfer_status == 'free':
        conditions.append(sql_text(f'NOT {owned}'))
        conditions.append(sql_text(f'NOT {active_listing}'))
    elif transfer_status == 'listed':
        conditions.append(sql_text(active_listing))
    elif transfer_status == 'not_for_sale':
        conditions.append(sql_text(owned))
        conditions.append(sql_text(f'NOT {active_listing}'))

    attributes = filters.get('attributes') or {}
    for name, spec in attributes.items():
        if name not in {'pace','shooting','passing','dribbling','defending','physical','stamina','mental'}:
            raise ValueError(f'Unsupported scouting attribute: {name}')
        condition = _range_condition(getattr(p.c, name), spec)
        if condition is not None:
            conditions.append(condition)

    query = select(*[getattr(p.c, name) for name in _PLAYER_COLUMNS])
    if conditions:
        query = query.where(and_(*conditions))
    # Hard deterministic order fixes page 5 -> 6 -> 5 drift.
    return query.order_by(p.c.id.asc())


def build_scout_count_query(filters=None):
    """Return a matching deterministic count query for the same filters."""
    base = build_scout_query(filters).order_by(None).subquery('scout_rows')
    return select(func.count()).select_from(base)


def build_scout_report(opponent_id, opponent_name, matches, player_events):
    """Build a compact, explainable opponent report from observed match data."""
    n=len(matches)
    formations=Counter(m.get('formation') for m in matches if m.get('formation'))
    styles=Counter(m.get('style') for m in matches if m.get('style'))
    formation=formations.most_common(1)[0][0] if formations else 'UNKNOWN'
    style=styles.most_common(1)[0][0] if styles else 'UNKNOWN'
    gf=sum(int(m.get('goals_for') or 0) for m in matches)
    ga=sum(int(m.get('goals_against') or 0) for m in matches)
    key=tuple(x[0] for x in sorted(player_events.items(), key=lambda kv:(-kv[1],kv[0]))[:5])
    tendencies=[]
    if n:
        if gf/n >= 1.75: tendencies.append('Сильная результативность')
        elif gf/n <= .85: tendencies.append('Невысокая результативность')
        if ga/n >= 1.50: tendencies.append('Уязвимость в обороне')
        elif ga/n <= .85: tendencies.append('Надёжная оборона')
    if formation in {'3-5-2','5-3-2'}: tendencies.append('Численное преимущество в центре/низкий блок')
    if formation=='4-3-3': tendencies.append('Опасность ширины и быстрых флангов')
    if formation=='4-2-3-1': tendencies.append('Опора на десятку между линиями')
    if style=='COUNTER': tendencies.append('Ожидает ошибки после потери мяча')
    if style=='POSSESSION': tendencies.append('Старается контролировать мяч')
    if style=='ATTACK': tendencies.append('Высокий атакующий риск')
    rec=[]
    if style=='COUNTER': rec.append('Не завышать линию без страховки')
    if formation=='4-3-3': rec.append('Закрывать фланги и пространство за крайними защитниками')
    if formation=='4-2-3-1': rec.append('Перекрыть передачу в зону AM')
    if formation in {'3-5-2','5-3-2'}: rec.append('Использовать ширину и перегружать фланги')
    if 'Надёжная оборона' in tendencies: rec.append('Увеличить терпение и качество последнего паса')
    if 'Уязвимость в обороне' in tendencies: rec.append('Повысить интенсивность прессинга')
    return ScoutingReport(opponent_id,opponent_name,n,formation,style,round(gf/n,2) if n else 0.0,round(ga/n,2) if n else 0.0,key,tuple(tendencies),tuple(rec))
