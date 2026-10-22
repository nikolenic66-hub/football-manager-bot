from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_regular_unique_index_migration_cleans_legacy_duplicates_and_recreates_index():
    sql = (ROOT / 'migrations' / '027_regular_unique_index.sql').read_text(encoding='utf-8')
    cleanup = sql.index('DELETE FROM club_players cp')
    drop = sql.index('DROP INDEX IF EXISTS one_copy_per_template_per_club;')
    create = sql.index('CREATE UNIQUE INDEX one_copy_per_template_per_club')

    assert cleanup < drop < create
    assert 'GROUP BY club_id, template_id' in sql
    assert 'SELECT MIN(id)' in sql
    assert 'ON club_players (club_id, template_id);' in sql
    assert 'WHERE template_id IS NOT NULL' not in sql


def test_regular_unique_index_migration_is_numbered_after_026():
    assert (ROOT / 'migrations' / '026_enforce_club_player_template_id.sql').exists()
    assert (ROOT / 'migrations' / '027_regular_unique_index.sql').exists()
    assert sorted(p.name for p in (ROOT / 'migrations').glob('027_*.sql')) == [
        '027_regular_unique_index.sql'
    ]
