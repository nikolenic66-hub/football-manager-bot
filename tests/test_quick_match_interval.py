from datetime import datetime, timezone, timedelta


def test_quick_league_schedule_is_10_minutes_apart():
    start = datetime(2026, 10, 19, 20, 0, tzinfo=timezone(timedelta(hours=3)))
    kickoffs = [start + timedelta(minutes=10 * round_no) for round_no in range(1, 31)]
    assert len(kickoffs) == 30
    assert all((b - a).total_seconds() == 600 for a, b in zip(kickoffs, kickoffs[1:]))


def test_normal_league_schedule_is_unchanged():
    start = datetime(2026, 10, 19, 20, 0, tzinfo=timezone(timedelta(hours=3)))
    interval_days = 2
    kickoffs = [start + timedelta(days=interval_days * round_no) for round_no in range(30)]
    assert all((b - a).total_seconds() == 2 * 86400 for a, b in zip(kickoffs, kickoffs[1:]))
