from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_sqlalchemy_pool_stays_below_postgres_connection_cap():
    source = (ROOT / 'app' / 'db.py').read_text(encoding='utf-8')
    assert 'pool_size=3' in source
    assert 'max_overflow=5' in source


def test_admin_id_has_no_hardcoded_default():
    source = (ROOT / 'app' / 'config.py').read_text(encoding='utf-8')
    assert 'admin_id: int | None = None' in source
    assert '6521319127' not in source


def test_admin_middleware_denies_when_admin_id_is_unconfigured():
    source = (ROOT / 'app' / 'handlers' / 'admin.py').read_text(encoding='utf-8')
    assert 'settings.admin_id is None or user != settings.admin_id' in source


def test_telegram_limiter_has_25_per_second_budget():
    source = (ROOT / 'app' / 'telegram_rate_limit.py').read_text(encoding='utf-8')
    assert 'max_messages=25' in source
    assert 'window_seconds=1.0' in source
