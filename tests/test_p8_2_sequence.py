from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_sequence_migration_has_stable_sequence_and_immutable_snapshots():
    sql = (ROOT / 'migrations/055_season_sequence.sql').read_text()
    assert 'CREATE TABLE IF NOT EXISTS season_sequences' in sql
    assert 'sequence_id BIGINT' in sql
    assert 'CREATE UNIQUE INDEX IF NOT EXISTS uq_seasons_sequence_number' in sql
    assert 'CREATE TABLE IF NOT EXISTS season_league_snapshots' in sql
    assert 'UNIQUE(season_id,league_id)' in sql
    assert 'season_league_snapshots are immutable' in sql
    assert 'BEFORE UPDATE OR DELETE' in sql


def test_new_season_stays_in_same_sequence():
    source = (ROOT / 'app/game/season.py').read_text()
    assert 'SELECT id,sequence_id,season_number FROM seasons ORDER BY id DESC LIMIT 1' in source
    assert 'async with session.begin_nested()' in source
    assert 'DELETE FROM matches WHERE season_id=:old' in source
    assert 'INSERT INTO seasons(sequence_id,season_number,state,season_seed,started_at)' in source
    assert 'UPDATE season_sequences SET current_season_id=:s' in source


def test_league_snapshot_is_insert_only():
    source = (ROOT / 'app/game/season.py').read_text()
    assert 'INSERT INTO season_league_snapshots' in source
    assert 'ON CONFLICT(season_id,league_id) DO NOTHING' in source
