from app.game.economy import (
    attendance, match_revenue, stadium_capacity, stadium_match_cost,
    sponsor_income, wage_bill, prize_money,
)


def test_ticket_price_has_demand_elasticity():
    low = attendance(10000, 12000, 4000, 8, 5, 10)
    high = attendance(10000, 12000, 4000, 8, 5, 100)
    assert low > high
    assert 500 <= high <= 10000


def test_stadium_capacity_and_match_cost():
    assert stadium_capacity(1) == 8000
    assert stadium_capacity(20) == 55500
    assert stadium_match_cost(3, 5000) == 25000


def test_revenue_and_sponsor_are_deterministic():
    assert match_revenue(5000, 25) == 125000
    assert sponsor_income(1, 1) == 25000
    assert sponsor_income(5, 10) == 150000


def test_wage_bill_is_period_fraction():
    assert wage_bill([100000, 200000], 0.25) == 75000
    assert wage_bill([100000, 200000]) == 75000


def test_prize_money_descends_by_position_and_sums_to_pool():
    prizes = [prize_money(i, 8) for i in range(1, 9)]
    assert prizes == sorted(prizes, reverse=True)
    assert sum(prizes) == 12_000_000
    assert all(x > 0 for x in prizes)
