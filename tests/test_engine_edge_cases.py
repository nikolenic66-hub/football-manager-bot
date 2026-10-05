from app.game.engine import simulate_first_half, simulate_second_half
from app.game.tactics import Tactics


def test_empty_sides_are_supported_without_crash():
    h, a, events = simulate_second_half([], [], Tactics(), Tactics(), 123)
    assert (h, a) == (0, 0)
    assert isinstance(events, list)


def test_red_and_injury_events_are_deterministic_and_ordered():
    from tests.test_engine import squad
    home, away = squad(1), squad(100)
    first = simulate_second_half(home, away, Tactics(), Tactics(), 42)
    second = simulate_second_half(home, away, Tactics(), Tactics(), 42)
    assert first == second
    events = first[2]
    from app.game.engine import _EVENT_ORDER
    assert events == sorted(events, key=lambda e: (e.minute, _EVENT_ORDER.get(e.type, 99)))


def test_first_half_with_empty_side_has_no_goals():
    h, a, events = simulate_first_half([], [], Tactics(), Tactics(), 77)
    assert h == a == 0
