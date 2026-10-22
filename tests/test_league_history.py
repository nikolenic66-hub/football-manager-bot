from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICES = (ROOT / 'app' / 'services.py').read_text(encoding='utf-8')
ADMIN = (ROOT / 'app' / 'handlers' / 'admin.py').read_text(encoding='utf-8')
MIGRATION = (ROOT / 'migrations' / '025_league_history.sql').read_text(encoding='utf-8')


def test_league_history_migration_has_no_fk_to_leagues_and_indexes_history():
    assert 'CREATE TABLE IF NOT EXISTS league_history' in MIGRATION
    assert 'league_id BIGINT NOT NULL' in MIGRATION
    assert 'REFERENCES leagues' not in MIGRATION
    assert 'CREATE INDEX IF NOT EXISTS idx_league_history_league' in MIGRATION
    assert 'CREATE INDEX IF NOT EXISTS idx_league_history_club' in MIGRATION


def test_archive_league_orders_archives_releases_and_finishes():
    block = SERVICES[SERVICES.index('async def archive_league'):SERVICES.index('async def fast_play_round')]
    assert 'FOR UPDATE' in block
    assert 'ORDER BY t.points DESC,(t.goals_for-t.goals_against) DESC,t.goals_for DESC,c.id' in block
    assert 'INSERT INTO league_history' in block
    assert 'enumerate(rows,1)' in block
    assert "SELECT id,name,status,season_number FROM leagues WHERE id=:l FOR UPDATE" in block
    assert "UPDATE leagues SET status='FINISHED',season_state='SEASON_END',finished_at=now()" in block


def test_final_round_archives_only_after_prizes_and_before_finished_state():
    block = SERVICES[SERVICES.index('async def finalize_live_round'):SERVICES.index('async def deliver_due_notifications')]
    assert 'await settle_season_prizes(s,league_id)' in block
    assert 'await archive_league(s,league_id)' in block
    assert block.index('await settle_season_prizes(s,league_id)') < block.index('await archive_league(s,league_id)')


def test_full_reset_does_not_archive_leagues_or_issue_normal_season_rewards():
    block = SERVICES[SERVICES.index('async def full_reset'):SERVICES.index('async def _starting_players')]
    assert 'archive_league(' not in block
    assert "'DELETE FROM leagues'" in block
    assert 'pg_advisory_xact_lock' in block
    assert 'ensure_initial_season(s)' in block

def test_admin_league_history_has_five_season_pages_and_finished_winner():
    assert "'📜 История сезонов'" in ADMIN
    assert "admin:league:{lid}:history:0" in ADMIN
    assert 'page*5:(page+1)*5' in ADMIN
    assert 'winner_name' in ADMIN
    assert 'победитель сезона' in ADMIN


def test_admin_finished_table_uses_archived_history():
    start = ADMIN.index('async def _admin_league_table')
    block = ADMIN[start:ADMIN.index('def _admin_round_text', start)]
    assert "league['status']=='FINISHED'" in block
    assert 'FROM league_history' in block
