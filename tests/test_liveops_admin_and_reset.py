from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

def test_admin_liveops_controls_all_multipliers_and_reset():
    source=(ROOT/'app/handlers/admin.py').read_text(encoding='utf-8')
    for key in ('match_ticket_multiplier','sponsor_multiplier','wage_multiplier','prize_multiplier','battle_pass_xp_multiplier','quest_reward_multiplier','pack_price_multiplier'):
        assert key in source
    assert "('🎮 LiveOps', 'admin:liveops')" in source
    assert 'admin:liveops:reset' in source
    assert 'INSERT INTO liveops_season_config' in source
    assert 'CAST(:c AS jsonb)' in source

def test_max_teams_migration_replaces_old_check_with_16_cap():
    migration=(ROOT/'migrations/057_fix_max_teams_check.sql').read_text(encoding='utf-8')
    assert 'DROP CONSTRAINT leagues_max_teams_check' in migration
    assert 'CHECK (max_teams BETWEEN 4 AND 16)' in migration

def test_full_reset_clears_new_season_liveops_and_tournament_state():
    source=(ROOT/'app/services.py').read_text(encoding='utf-8')
    block=source[source.index('async def full_reset'):source.index('async def _starting_players')]
    for table in ('season_events','liveops_events','liveops_season_config','champions_matches','champions_groups','champions_league','cup_matches','cups','daily_quests','season_league_snapshots','player_season_state','season_history','season_sequences','seasons'):
        assert f'DELETE FROM {table}' in block
    assert 'UPDATE season_pass_progress SET points=0,level=0' in block
    assert 'DELETE FROM season_pass_rewards' not in block
    assert 'DELETE FROM season_pass' not in block
    assert 'archive_league(' not in block
    assert '_assign_fresh_leagues_to_real_players' in block


def test_full_reset_assigns_real_clubs_before_bot_fillers_and_admin_allows_active_leagues():
    services=(ROOT/'app/services.py').read_text(encoding='utf-8')
    admin=(ROOT/'app/handlers/admin.py').read_text(encoding='utf-8')
    helper=services[services.index('async def _assign_fresh_leagues_to_real_players'):services.index('async def full_reset')]
    assert 'u.telegram_id > 0' in helper
    assert 'u.telegram_id < 0' in helper
    assert 'ORDER BY c.owner_user_id' in helper
    assert 'while len(member_ids) < 16' in helper
    assert "_start_league_by_id(s, league_id, start_global_competitions=False)" in helper
    prepare=admin[admin.index("async def admin_full_reset_prepare"):admin.index("@admin_router.callback_query(F.data == 'admin:system:reset:yes')")]
    assert "SELECT 1 FROM leagues WHERE status='ACTIVE'" not in prepare
    assert 'немедленно остановит активные лиги и матчи' in prepare
    assert 'все реальные клубы будут заново распределены' in prepare or 'Все реальные клубы будут заново распределены' in prepare


def test_live_worker_serializes_scheduler_writes_with_full_reset():
    worker=(ROOT/'app/live_worker.py').read_text(encoding='utf-8')
    lock="pg_advisory_xact_lock(hashtextextended('football_manager_full_reset', 0))"
    assert worker.count(lock) >= 4
    assert worker.index(lock) < worker.index('recover_daily_fitness(s)')
