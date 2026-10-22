from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_migrations_are_versioned_and_legacy_bootstrap_is_not_replayed():
    source=(ROOT/'app'/'db.py').read_text()
    assert 'schema_migrations' in source
    assert 'version <= 11' in source
    assert 'pool_recycle=300' in source
    assert 'pool_pre_ping=True' in source
    assert 'statement_cache_size' in source


def test_compose_does_not_hardcode_database_password_or_expose_port():
    compose=(ROOT/'docker-compose.yml').read_text()
    assert 'POSTGRES_PASSWORD: ${POSTGRES_PASSWORD' in compose
    assert '# ports:' not in compose
    assert 'ports:' not in compose


def test_release_workflow_validates_on_release_branch_before_promoting_main():
    workflow = (Path(__file__).resolve().parents[1] / '.github' / 'workflows' / 'main.yml').read_text()
    assert 'branches:\n      - release' in workflow
    assert 'git push origin HEAD:main' in workflow
    assert 'environment: production' in workflow
    assert 'pytest -q' in workflow
