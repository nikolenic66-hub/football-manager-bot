"""Club infrastructure progression."""
FACILITIES = {
    'vip': ('vip_level', 'VIP', 250_000),
    'medical': ('medical_level', 'Медицина', 300_000),
    'training': ('training_level', 'Тренировки', 350_000),
    'academy': ('academy_level', 'Академия', 400_000),
}


def facility_cost(facility, level):
    if facility not in FACILITIES:
        raise ValueError('Unknown facility.')
    return FACILITIES[facility][2] * max(1, int(level))


async def upgrade_facility(club, facility, session):
    if facility not in FACILITIES:
        raise ValueError('Unknown facility.')
    column, label, _ = FACILITIES[facility]
    club_id = int(club['id'] if isinstance(club, dict) else club.id)
    row = (await session.execute(__import__('sqlalchemy').text(f'SELECT {column},budget FROM clubs WHERE id=:c FOR UPDATE'), {'c': club_id})).mappings().first()
    if not row:
        raise ValueError('Club not found.')
    level = int(row[column])
    if level >= 10:
        raise ValueError(f'{label} уже на максимальном уровне.')
    cost = facility_cost(facility, level)
    if int(row['budget']) < 0:
        from app.services import BANKRUPTCY_WARNING, check_bankruptcy
        if check_bankruptcy(row):
            raise ValueError(BANKRUPTCY_WARNING)
    if int(row['budget']) < cost:
        raise ValueError(f'Недостаточно бюджета: нужно €{cost:,}.')
    await session.execute(__import__('sqlalchemy').text(f'UPDATE clubs SET {column}={column}+1 WHERE id=:c'), {'c': club_id})
    await session.execute(__import__('sqlalchemy').text('UPDATE clubs SET budget=budget-:v WHERE id=:c'), {'v': cost, 'c': club_id})
    return level + 1, cost


def ticket_income_multiplier(level):
    return 1.0 + 0.05 * max(0, int(level) - 1)

def recovery_multiplier(level):
    return max(0.5, 1.0 - 0.05 * max(0, int(level) - 1))

def training_multiplier(level):
    return 1.0 + 0.04 * max(0, int(level) - 1)

def academy_quality_bonus(level):
    return 2 * max(0, int(level) - 1)
