from app.game.engine import simulate_second_half
from app.game.tactics import Tactics
from tests.test_engine import squad


def test_second_half_returns_deterministic_injury_time_and_added_time_events():
    home, away = squad(1), squad(100)
    result = simulate_second_half(
        home, away, Tactics(), Tactics(), 1,
        ai_commentary=False, return_injury_time=True,
    )
    home_goals, away_goals, events, injury_time = result
    assert 1 <= injury_time <= 5
    assert isinstance(home_goals, int) and isinstance(away_goals, int)
    added = [event for event in events if event.minute > 90]
    assert added
    assert max(event.minute for event in events) <= 90 + injury_time
    assert any(event.metadata.get('is_added_time') for event in added)
    assert all(
        event.text.startswith(f"90+{event.minute - 90}′")
        for event in added if event.metadata.get('is_added_time')
    )
    again = simulate_second_half(
        home, away, Tactics(), Tactics(), 1,
        ai_commentary=False, return_injury_time=True,
    )
    assert result == again


def test_added_time_is_persisted_without_changing_existing_event_schema_contract():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    migration = (root / "migrations" / "059_match_events_added_time.sql").read_text(encoding="utf-8")
    services = (root / "app" / "services.py").read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS is_added_time BOOLEAN NOT NULL DEFAULT FALSE" in migration
    assert services.count("metadata,is_added_time)") >= 2
    assert "'added':is_added_time" in services
    assert "'added':bool((meta or {}).get('extra_time')) or int(mi)>90" in services
