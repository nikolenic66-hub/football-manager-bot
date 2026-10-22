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
    assert 'FROM notifications n' in block
    assert 'FOR UPDATE SKIP LOCKED' in block
    assert 'LEFT JOIN notification_settings ns' not in block.split('FOR UPDATE SKIP LOCKED',1)[0]
    assert 'processing_at=now()' in block
    assert 'await s.commit()' in block
    assert 'next_attempt_at=now()+make_interval' in block

def test_worker_logs_failures_instead_of_silently_swallowing_them():
    source=(ROOT/'app'/'live_worker.py').read_text(encoding='utf-8')
    assert "logger.exception('Live worker iteration failed')" in source

def test_dangerous_shots_are_filtered_before_notification():
    source=(ROOT/'app'/'live_worker.py').read_text(encoding='utf-8')
    assert "DANGEROUS_SHOT_RESULTS={'GOAL','SAVED','BLOCKED'}" in source
    assert "meta.get('result') not in DANGEROUS_SHOT_RESULTS" not in source
    assert "important=[e for e in events if e['event_type'] in {'GOAL','RED'}]" in source
    assert "important.extend(e for e in events if e['event_type']=='YELLOW')" in source

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
    assert 'ORDER BY e.minute,e.id' in source
    assert "goal_scores[event['id']]=(gh,ga)" in source
    assert "event_home+=int(row['home_halftime_score'] or 0)" in source


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


def test_start_round_halftime_has_no_telegram_user_lookup_dependency():
    source=(ROOT/'app'/'services.py').read_text(encoding='utf-8')
    start=source.index('async def start_round_halftime')
    end=source.index('async def change_halftime_tactics', start)
    block=source[start:end]
    assert 'current_club(' not in block
    assert 'club_by_tg(' not in block
    assert "m['home_club_id']" in block
    assert "m['away_club_id']" in block


def test_playround_callback_passes_real_telegram_user():
    source=(ROOT/'app'/'bot.py').read_text(encoding='utf-8')
    block=source[source.index("@router.callback_query(F.data=='league:play')"):]
    block=block[:block.index("@router.callback_query", 10)]
    assert 'await cmd_playround(c.message, from_user=c.from_user)' in block


def test_live_worker_logs_due_started_and_failed_counts():
    source=(ROOT/'app'/'live_worker.py').read_text(encoding='utf-8')
    assert 'due_matches=%s started_rounds=%s failed_rounds=%s' in source
    assert 'logger.exception(\'Failed to start due match round' in source



def test_finance_helper_resolves_club_by_telegram_id():
    source=(ROOT/'app'/'bot.py').read_text(encoding='utf-8')
    start=source.index('async def _show_finance')
    end=source.index('def _finance_keyboard', start) if 'def _finance_keyboard' in source[start:] else start+700
    block=source[start:end]
    assert 'club_by_tg(s,telegram_id)' in block
    assert 'current_club(s,telegram_id)' not in block


def test_live_match_rating_import_is_present():
    source=(ROOT/'app'/'services.py').read_text(encoding='utf-8')
    assert 'from app.game.ratings import player_rating' in source
    start=source.index('async def _finish_live_match')
    end=source.index('async def advance_live_matches', start)
    block=source[start:end]
    assert 'player_rating(p_obj)' in block


def test_bot_imports_style_sliders_used_by_style_handlers():
    source=(ROOT/'app'/'bot.py').read_text(encoding='utf-8')
    assert 'from .game.tactics import Tactics, Style, STYLE_SLIDERS' in source
    assert 'STYLE_SLIDERS[style_enum]' in source
    assert 'STYLE_SLIDERS[style]' in source


def test_live_worker_has_failure_backoff_and_recovery():
    source=(ROOT/'app'/'live_worker.py').read_text(encoding='utf-8')
    assert 'consecutive_failures=0' in source
    assert 'last_failure_signature=None' in source
    assert "signature=f'{type(exc).__name__}:{exc}'" in source
    assert 'if consecutive_failures < 3:' in source
    assert 'sleep_for=60.0 if consecutive_failures == 3 else 120.0' in source
    assert 'now_mono-last_failure_log_at >= 60.0' in source
    assert 'consecutive_failures=0' in source
    assert 'Live worker recovered after %s consecutive failures.' in source


def test_live_events_are_materialized_only_as_minutes_are_reached():
    source=(ROOT/'app'/'services.py').read_text(encoding='utf-8')
    start=source.index('async def start_round_halftime')
    end=source.index('async def change_halftime_tactics',start)
    kickoff=source[start:end]
    advance=source[source.index('async def advance_live_matches'):source.index('# --- Club management expansion')]
    assert 'halftime_data=:data' in kickoff
    assert 'INSERT INTO match_events' not in kickoff
    assert '_materialize_live_events(s,match,phase_data,minute)' in advance
    assert 'current+min(3,max(0,wall_target-current))' in advance


def test_live_event_text_normalizes_literal_newline_sequences():
    source=(ROOT/'app'/'live_worker.py').read_text(encoding='utf-8')
    assert "replace(r'\\n','\\n')" in source
    assert "body=f\"{minute_label}′ {pname}\\n{score}\"" in source


def test_live_event_score_is_calculated_at_event_minute_and_id():
    source=(ROOT/'app'/'live_worker.py').read_text(encoding='utf-8')
    assert 'ORDER BY e.minute,e.id' in source
    assert "if (int(goal['minute']),int(goal['id'])) > (int(event['minute']),int(event['id']))" in source
    assert "goal_scores[event['id']]=(gh,ga)" in source


def test_live_event_notifications_are_scoped_to_one_match_and_cursor():
    source=(ROOT/'app'/'live_worker.py').read_text(encoding='utf-8')
    start=source.index('async def queue_live_event_notifications')
    end=source.index('async def _queue_for_club',start)
    block=source[start:end]
    assert 'WHERE m.id=:m FOR UPDATE' in block
    assert 'WHERE e.match_id=:m AND e.id>:cursor' in block
    assert 'UPDATE matches SET live_event_cursor=:id WHERE id=:m' in block


def test_live_notifications_are_batched_and_sent_concurrently():
    worker=(ROOT/'app'/'live_worker.py').read_text(encoding='utf-8')
    services=(ROOT/'app'/'services.py').read_text(encoding='utf-8')
    assert 'ORDER BY e.minute,e.id LIMIT 2' in worker
    assert 'results=await asyncio.gather' in services
    assert 'send them concurrently' in services



def test_live_finish_has_terminal_guard_for_90_plus():
    source=(ROOT/'app'/'services.py').read_text(encoding='utf-8')
    start=source.index('async def _force_finish_live_match(s, match_id):')
    end=source.index('async def _finish_live_match(s, match):', start)
    block=source[start:end]
    assert "status='FINISHED'" in block
    assert "current_minute=:minute" in block
    assert 'async def _finish_live_match_emergency(s, match):' in block
    advance=source[source.index('async def _advance_live_matches_once'):source.index('async def advance_live_matches')]
    assert "await _finish_live_match_emergency(s,match)" in advance
    assert "await _force_finish_live_match(s,match['id'])" in advance


def test_force_finish_guard_handles_100_synthetic_90_minute_matches():
    import asyncio
    from app.services import _force_finish_live_match

    class Result:
        def __init__(self, row=None): self._row=row
        def mappings(self): return self
        def first(self): return self._row

    class FakeSession:
        def __init__(self): self.finished=[]
        async def execute(self, statement, params=None):
            sql=str(statement)
            if sql.startswith('SELECT id,home_score'):
                mid=int(params['m'])
                return Result({'id':mid,'home_score':1,'away_score':0,'home_xg':1.0,'away_xg':0.2})
            if sql.startswith('UPDATE matches'):
                self.finished.append(int(params['m']))
                return Result()
            raise AssertionError(sql)

    async def run():
        s=FakeSession()
        for match_id in range(1,101):
            await _force_finish_live_match(s,match_id)
        assert s.finished == list(range(1,101))
    asyncio.run(run())
