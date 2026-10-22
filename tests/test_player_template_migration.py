from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def test_player_template_migration_only_promotes_seed_data_sources():
    sql = (ROOT / 'migrations' / '020_player_templates.sql').read_text(encoding='utf-8')
    match = re.search(
        r"UPDATE players\s+SET is_seed=TRUE\s+WHERE\s+id<=1248\s+AND\s+data_source IN \(([^)]+)\);",
        sql,
        re.IGNORECASE,
    )
    assert match, 'migration 020 must gate legacy seed IDs by seed data_source'
    sources = {item.strip().strip("'") for item in match.group(1).split(',')}
    assert sources == {'seed', 'curated_real'}
    assert 'generated' not in sources
    assert "UPDATE players\nSET is_template=TRUE, template_id=NULL\nWHERE is_seed=TRUE;" in sql

    # Regression simulation: both the real admin marker and the legacy generated
    # admin marker must stay outside the seed predicate.
    rows = [
        {'id': 500, 'data_source': 'admin'},
        {'id': 500, 'data_source': 'admin'},
        {'id': 100, 'data_source': 'seed'},
        {'id': 100, 'data_source': 'curated_real'},
    ]
    promoted = [
        row for row in rows
        if row['id'] <= 1248 and row['data_source'] in sources
    ]
    assert promoted == rows[2:]


def test_club_player_template_migration_cleans_duplicates_before_unique_index():
    sql = (ROOT / 'migrations' / '022_club_player_templates.sql').read_text(encoding='utf-8')
    cleanup = sql.index('DELETE FROM club_players cp')
    unique_index = sql.index('CREATE UNIQUE INDEX IF NOT EXISTS one_copy_per_template_per_club')
    assert cleanup < unique_index
    block = sql[cleanup:unique_index]
    assert 'keep.club_id=cp.club_id' in block
    assert 'keep.template_id=cp.template_id' in block
    assert 'keep.player_id < cp.player_id' in block


def test_force_player_sequence_migration_is_idempotent_by_construction():
    sql = (ROOT / 'migrations' / '024_force_player_sequence_sync.sql').read_text(encoding='utf-8')
    assert "setval(" in sql
    assert "players_id_seq" in sql
    assert "MAX(id) FROM players" in sql


def test_seed_marker_repair_is_numbered_after_sequence_sync():
    assert (ROOT / 'migrations' / '023_seed_marker_repair.sql').exists()
    sql = (ROOT / 'migrations' / '023_seed_marker_repair.sql').read_text(encoding='utf-8')
    assert "data_source='admin'" in sql
    assert "data_source IN ('seed','curated_real')" in sql


def test_club_player_template_index_is_non_partial_after_026():
    migration_022 = (ROOT / 'migrations' / '022_club_player_templates.sql').read_text(encoding='utf-8')
    migration_026 = (ROOT / 'migrations' / '026_enforce_club_player_template_id.sql').read_text(encoding='utf-8')
    assert 'ON club_players(club_id, template_id);' in migration_022
    assert 'WHERE template_id IS NOT NULL' not in migration_022
    assert 'ALTER TABLE club_players ALTER COLUMN template_id SET NOT NULL;' in migration_026
    assert 'DROP INDEX IF EXISTS one_copy_per_template_per_club;' in migration_026
    assert 'CREATE UNIQUE INDEX one_copy_per_template_per_club' in migration_026
    assert 'WHERE template_id IS NOT NULL' not in migration_026
