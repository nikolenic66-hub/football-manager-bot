from sqlalchemy import text

STAFF_TYPES={
    'training':('staff_training','Тренеры',200_000),
    'medical':('staff_medical','Медики',220_000),
    'scouting':('staff_scouting','Скауты',180_000),
}


def staff_bonus(staff_type, level) -> float:
    staff_type=str(staff_type).lower()
    level=max(1,min(10,int(level)))
    if staff_type=='training':
        return 1.0 + 0.05*level
    if staff_type=='medical':
        return max(0.0,1.0 - 0.10*level)
    if staff_type=='scouting':
        return 1.0 + 0.05*level
    raise ValueError('Unknown staff type.')


def staff_cost(staff_type, level) -> int:
    key=str(staff_type).lower()
    if key not in STAFF_TYPES:
        raise ValueError('Unknown staff type.')
    return STAFF_TYPES[key][2] * max(1,int(level))


async def upgrade_staff(club, staff_type, session):
    key=str(staff_type).lower()
    if key not in STAFF_TYPES:
        raise ValueError('Unknown staff type.')
    column,label,_=STAFF_TYPES[key]
    row=(await session.execute(text(f'SELECT id,budget,{column} AS level FROM clubs WHERE id=:c FOR UPDATE'),{'c':club['id']})).mappings().first()
    if not row:
        raise ValueError('Club not found.')
    level=int(row['level'] or 1)
    if level>=10:
        raise ValueError(f'{label} уже на максимальном уровне.')
    cost=staff_cost(key,level+1)
    if int(row['budget'])<cost:
        raise ValueError(f'Недостаточно бюджета. Нужно €{cost:,}.')
    await session.execute(text(f'UPDATE clubs SET {column}={column}+1,budget=budget-:cost WHERE id=:c'),{'cost':cost,'c':club['id']})
    return level+1,cost


def scouting_discovery_chance(level) -> float:
    return min(0.50,max(0.05,staff_bonus('scouting',level)-1.0))
