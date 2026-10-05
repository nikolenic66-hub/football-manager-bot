from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def test_realtime_migration_contains_persistent_clock_state():
    sql=(ROOT/'migrations'/'008_realtime_live.sql').read_text(encoding='utf-8')
    for field in ('live_phase','phase_started_at','halftime_ends_at','second_half_started_at','second_half_data','live_event_cursor'):
        assert field in sql
    assert "FIRST_HALF" in sql and "HALFTIME" in sql and "SECOND_HALF" in sql

def test_bot_starts_persistent_live_worker():
    source=(ROOT/'app'/'bot.py').read_text(encoding='utf-8')
    assert "football-manager-live-worker" in source
    assert 'dp.startup.register(on_startup)' in source
    assert 'dp.shutdown.register(on_shutdown)' in source

def test_live_worker_broadcasts_only_important_events():
    source=(ROOT/'app'/'live_worker.py').read_text(encoding='utf-8')
    for event in ('GOAL','ASSIST','SHOT','YELLOW','RED','INJURY','SUBSTITUTION'):
        assert event in source


def test_calendar_kickoff_is_preserved_when_match_starts():
    source=(ROOT/'app'/'services.py').read_text(encoding='utf-8')
    start=source.index('async def start_round_halftime')
    end=source.index('async def change_halftime_tactics', start)
    block=source[start:end]
    assert 'scheduled_at' in block
    assert 'scheduled_at=NULL' not in block

def test_notification_delivery_has_retry_state_and_no_long_db_lock():
    migration=(ROOT/'migrations'/'010_notification_delivery.sql').read_text(encoding='utf-8')
    source=(ROOT/'app'/'services.py').read_text(encoding='utf-8')
    assert 'delivery_attempts' in migration
    assert 'processing_at' in migration
    assert 'next_attempt_at' in migration
    block=source[source.index('async def deliver_due_notifications'):source.index('# --- Notifications', source.index('async def deliver_due_notifications'))]
    assert 'FOR UPDATE OF n SKIP LOCKED' in block
    assert 'processing_at=now()' in block
    assert 'await s.commit()' in block
    assert 'next_attempt_at=now()+make_interval' in block

def test_worker_logs_failures_instead_of_silently_swallowing_them():
    source=(ROOT/'app'/'live_worker.py').read_text(encoding='utf-8')
    assert "logger.exception('Live worker iteration failed')" in source

def test_dangerous_shots_are_filtered_before_notification():
    source=(ROOT/'app'/'live_worker.py').read_text(encoding='utf-8')
    assert "DANGEROUS_SHOT_RESULTS={'GOAL','SAVED','BLOCKED'}" in source
    assert "meta.get('result') not in DANGEROUS_SHOT_RESULTS" in source

def test_notification_callback_validates_category():
    source=(ROOT/'app'/'bot.py').read_text(encoding='utf-8')
    assert "allowed={'match','injury','transfer','finance','development','discipline','morale','tournament','list'}" in source
    assert "if action not in allowed:" in source

def test_notification_history_excludes_future_scheduled_items():
    source=(ROOT/'app'/'services.py').read_text(encoding='utf-8')
    assert 'FROM notifications WHERE user_id=:u AND deliver_at<=now()' in source
    assert 'read_at IS NULL AND deliver_at<=now()' in source


def test_notification_delivery_rechecks_current_category_preference():
    source=(ROOT/'app'/'services.py').read_text(encoding='utf-8')
    block=source[source.index('async def deliver_due_notifications'):source.index('# --- Notifications', source.index('async def deliver_due_notifications'))]
    assert 'LEFT JOIN notification_settings ns' in block
    assert "WHEN 'MATCH' THEN COALESCE(ns.match,TRUE)" in block
    assert "WHEN 'INJURY' THEN COALESCE(ns.injury,TRUE)" in block


def test_live_goal_notification_score_uses_complete_event_history():
    source=(ROOT/'app'/'live_worker.py').read_text(encoding='utf-8')
    assert 'FROM match_events' in source
    assert "id<=:id" in source
    assert 'prior_home=max(0,prior_home-int(row[\'home_halftime_score\'] or 0))' in source


def test_second_half_excludes_first_half_red_cards_and_injuries():
    source=(ROOT/'app'/'services.py').read_text(encoding='utf-8')
    start=source.index('async def _prepare_second_half_live')
    end=source.index('    hrow=match[\'home_second_tactics\']',start)
    block=source[start:end]
    assert "minute<=45" in block
    assert "event_type IN ('RED','INJURY')" in block
    assert 'home=[p for p in original_home if p.id not in unavailable_by_club' in block
    assert 'away=[p for p in original_away if p.id not in unavailable_by_club' in block


def test_old_instant_match_lifecycle_is_removed():
    source=(ROOT/'app'/'services.py').read_text(encoding='utf-8')
    for name in ('async def play_round', 'async def resume_match', 'async def resume_round', 'async def force_resume_live_round'):
        assert name not in source

def test_notification_lifecycle_has_cancel_and_failure_state():
    migration=(ROOT/'migrations'/'011_notification_lifecycle.sql').read_text(encoding='utf-8')
    source=(ROOT/'app'/'services.py').read_text(encoding='utf-8')
    assert 'cancelled_at' in migration and 'failed_at' in migration
    assert 'cancelled_at=COALESCE(cancelled_at,now())' in source
    assert 'failed_at=now()' in source


def test_recovery_notifications_are_emitted_when_match_countdown_reaches_zero():
    source=(ROOT/'app'/'services.py').read_text(encoding='utf-8')
    assert "if int(rr['injury_matches'] or 0)==1" in source
    assert 'Игрок восстановился' in source
    assert "if int(rr['suspension_matches'] or 0)==1" in source
    assert 'Дисквалификация закончилась' in source

def test_kickoff_cancels_stale_ten_minute_reminders():
    source=(ROOT/'app'/'services.py').read_text(encoding='utf-8')
    start=source.index('async def start_round_halftime')
    end=source.index('async def change_halftime_tactics',start)
    block=source[start:end]
    assert 'UPDATE notifications SET cancelled_at=COALESCE(cancelled_at,now())' in block
    assert "match-start-10:{league_id}:{round_no}:%" in block
