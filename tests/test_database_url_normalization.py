from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_database_url_validator_normalizes_managed_postgres_urls():
    source = (ROOT / "app" / "config.py").read_text(encoding="utf-8")
    assert "value = 'postgresql+asyncpg://' + value[len('postgres://'):" in source
    assert "value = 'postgresql+asyncpg://' + value[len('postgresql://'):" in source
    assert "DATABASE_URL must be a PostgreSQL URL using asyncpg." in source


def test_async_engine_is_configured_for_resilient_postgres_connections():
    source = (ROOT / "app" / "db.py").read_text(encoding="utf-8")
    assert "pool_pre_ping=True" in source
    assert "pool_recycle=300" in source
    assert 'connect_args={"statement_cache_size": 0}' in source
