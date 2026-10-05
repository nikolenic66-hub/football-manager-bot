from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_migrations_are_versioned_and_legacy_bootstrap_is_not_replayed():
    source=(ROOT/'app'/'db.py').read_text()
    assert 'schema_migrations' in source
    assert 'version <= 11' in source
    assert 'pool_recycle=1800' in source


def test_compose_does_not_hardcode_database_password_or_expose_port():
    compose=(ROOT/'docker-compose.yml').read_text()
    assert 'POSTGRES_PASSWORD: ${POSTGRES_PASSWORD' in compose
    assert '# ports:' not in compose
    assert 'ports:' not in compose
