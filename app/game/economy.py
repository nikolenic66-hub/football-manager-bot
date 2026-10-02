from dataclasses import dataclass

TICKET_MIN = 5
TICKET_MAX = 120
STADIUM_BASE_CAPACITY = 8_000
STADIUM_CAPACITY_STEP = 2_500

@dataclass(frozen=True)
class MatchEconomy:
    capacity: int
    attendance: int
    ticket_price: int
    ticket_revenue: int
    stadium_cost: int


def stadium_capacity(stadium_level: int) -> int:
    level = max(1, min(20, int(stadium_level)))
    return STADIUM_BASE_CAPACITY + (level - 1) * STADIUM_CAPACITY_STEP


def attendance(capacity, home_fans, away_fans, home_reputation, away_reputation, ticket_price=20):
    capacity = max(500, int(capacity))
    ticket_price = max(TICKET_MIN, min(TICKET_MAX, int(ticket_price)))
    base_demand = .42 * max(0, home_fans) + .12 * max(0, away_fans) + 350 * max(1, home_reputation) + 140 * max(1, away_reputation)
    price_factor = max(.28, min(1.18, 1.18 - (ticket_price - TICKET_MIN) / 105))
    demand = int(base_demand * price_factor)
    return min(capacity, max(500, demand))


def match_revenue(attendance, ticket_price=20):
    return max(0, int(attendance)) * max(TICKET_MIN, min(TICKET_MAX, int(ticket_price)))


def stadium_match_cost(stadium_level: int, attendance_value: int) -> int:
    level = max(1, int(stadium_level))
    return 5_000 * level + 2 * max(0, int(attendance_value))


def sponsor_income(sponsor_level: int, reputation: int) -> int:
    level = max(1, min(10, int(sponsor_level)))
    rep = max(1, int(reputation))
    return 20_000 * level + 5_000 * min(rep, 20)


def wage_bill(salaries, period_fraction=0.25) -> int:
    total = sum(max(0, int(x or 0)) for x in salaries)
    return int(total * max(0.0, min(1.0, float(period_fraction))))


def prize_money(position: int, teams: int) -> int:
    """Season prize, deliberately progressive but not so large that transfers become irrelevant."""
    n = max(4, min(12, int(teams)))
    pos = max(1, min(n, int(position)))
    pool = 1_500_000 * n
    weights = [max(1, n + 1 - i) for i in range(1, n + 1)]
    total_weight = sum(weights)
    base = (pool * weights[pos - 1]) // total_weight
    remainder = pool - sum((pool * w) // total_weight for w in weights)
    return base + (1 if pos <= remainder else 0)


def calculate_match_economy(club, opponent, home=True) -> MatchEconomy:
    capacity = stadium_capacity(club['stadium_level'])
    att = attendance(capacity, club['fans'], opponent['fans'], club['reputation'], opponent['reputation'], club['ticket_price']) if home else 0
    revenue = match_revenue(att, club['ticket_price']) if home else 0
    cost = stadium_match_cost(club['stadium_level'], att) if home else 0
    return MatchEconomy(capacity, att, club['ticket_price'], revenue, cost)
