from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_test_data_migration_is_opt_in_and_friendly_matches_are_supported():
    migration = (ROOT / 'migrations' / '065_test_data_and_friendly_matches.sql').read_text()
    assert 'clubs ADD COLUMN IF NOT EXISTS is_test BOOLEAN NOT NULL DEFAULT FALSE' in migration
    assert 'leagues ADD COLUMN IF NOT EXISTS is_test BOOLEAN NOT NULL DEFAULT FALSE' in migration
    assert 'matches ADD COLUMN IF NOT EXISTS is_test BOOLEAN NOT NULL DEFAULT FALSE' in migration
    assert "'FRIENDLY'" in migration
    assert "UPDATE leagues SET is_test=TRUE WHERE name ILIKE 'FM27 Quick Test%'" in migration


def test_cleanup_and_test_match_are_explicitly_tagged_and_do_not_delete_players_or_users():
    services = (ROOT / 'app' / 'services.py').read_text()
    cleanup = services.split('async def clean_test_data(s):', 1)[1].split('async def archive_league', 1)[0]
    create_match = services.split('async def create_admin_test_match(s, admin_telegram_id):', 1)[1].split('async def delete_admin_test_match', 1)[0]
    assert 'WHERE is_test=TRUE' in cleanup
    assert 'DELETE FROM players' not in cleanup
    assert 'DELETE FROM users' not in cleanup
    assert 'DELETE FROM matches WHERE id=ANY(:ids) AND is_test=TRUE' in cleanup
    assert 'DELETE FROM cup_matches' not in cleanup
    assert 'DELETE FROM champions_matches' not in cleanup
    assert 'NOT EXISTS (SELECT 1 FROM league_history lh WHERE lh.club_id=c.id)' in cleanup
    assert 'NOT EXISTS (SELECT 1 FROM champions_participants chp WHERE chp.club_id=c.id)' in cleanup
    assert "'FRIENDLY'" in create_match
    assert 'VALUES(NULL,NULL,0' in create_match
    assert 'is_test=TRUE' in create_match
    assert 'award_match_points' not in create_match


def test_admin_test_tools_are_restricted_to_the_fixed_admin_id():
    admin = (ROOT / 'app' / 'handlers' / 'admin.py').read_text()
    assert 'TEST_DATA_ADMIN_ID = 6521319127' in admin
    assert "Command('admin_clean_test')" in admin
    assert "Command('admin_test_match')" in admin
    assert 'clean_test_confirm' in admin


def test_match_canvas_uses_api_match_props_and_real_score_clock():
    canvas = (ROOT / 'mini_app' / 'src' / 'components' / 'MatchCanvas.tsx').read_text()
    screen = (ROOT / 'mini_app' / 'src' / 'ClubScreen.tsx').read_text()
    assert 'type MatchCanvasProps = { match: NextMatch | null }' in canvas
    assert 'match.home_score' in canvas and 'match.current_minute' in canvas
    assert '<MatchCanvas match={nextMatch}/>' in screen


def test_existing_connection_and_loading_fixes_remain_present():
    db = (ROOT / 'app' / 'db.py').read_text()
    worker = (ROOT / 'app' / 'live_worker.py').read_text()
    loading = (ROOT / 'mini_app' / 'src' / 'components' / 'LoadingAnimation.tsx').read_text()
    assert 'pool_pre_ping=True' in db
    assert 'pool_recycle=300' in db
    assert 'connect_args={"statement_cache_size": 0}' in db
    unlock = worker.split('pg_advisory_unlock(hashtext', 1)[1].split('finally:', 1)[0]
    assert 'except Exception' in unlock
    assert 'src="/football.png"' in loading
