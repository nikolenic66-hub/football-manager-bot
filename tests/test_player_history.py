from datetime import datetime, timezone
from app.game.player_history import format_history


def test_history_format_shows_development_path():
    history=[
        {'recorded_at':datetime(2026,5,1,tzinfo=timezone.utc),'ovr':67},
        {'recorded_at':datetime(2027,5,1,tzinfo=timezone.utc),'ovr':72},
        {'recorded_at':datetime(2028,5,1,tzinfo=timezone.utc),'ovr':78},
    ]
    assert format_history(history)=='2026: 67 → 2027: 72 → 2028: 78'
