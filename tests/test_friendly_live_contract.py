from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_user_friendly_match_starts_as_live_and_uses_paced_first_half():
    source = (ROOT / "app/services.py").read_text(encoding="utf-8")
    start = source.index('async def create_user_friendly_match')
    end = source.index('async def create_admin_test_match', start)
    body = source[start:end]
    assert "simulate_first_half" in body
    assert "simulate_match(" not in body
    assert "VALUES(NULL,NULL,0,:home,:away,:seed,'LIVE','FRIENDLY'" in body
    assert '"status":"live"' in body
    assert "home_halftime_score" in body
    assert "halftime_data" in body


def test_friendly_idempotency_reuses_active_live_match():
    source = (ROOT / "app/services.py").read_text(encoding="utf-8")
    start = source.index('async def create_user_friendly_match')
    end = source.index('async def create_admin_test_match', start)
    body = source[start:end]
    assert "m.status='LIVE'" in body
    assert "m.status='FINISHED' AND m.finished_at >= now()-INTERVAL '10 seconds'" in body


def test_mini_app_reports_live_start_instead_of_finished():
    source = (ROOT / "mini_app/src/ClubScreen.tsx").read_text(encoding="utf-8")
    assert "Товарищеский матч начался — LIVE" in source
    assert "Тестовый матч начался — LIVE" in source
