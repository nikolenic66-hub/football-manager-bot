from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
LIVE=(ROOT/'app/live_worker.py').read_text(encoding='utf-8')
SERVICES=(ROOT/'app/services.py').read_text(encoding='utf-8')
BOT=(ROOT/'app/bot.py').read_text(encoding='utf-8')


def test_live_private_notifications_only_goal_and_red():
    start=LIVE.index('async def queue_live_event_notifications')
    end=LIVE.index('async def _queue_for_club', start)
    block=LIVE[start:end]
    assert "important=[e for e in events if e['event_type'] in {'GOAL','RED'}]" in block
    assert "if e['event_type']=='GOAL'" in block
    assert "elif e['event_type']=='RED'" in block
    assert "'SHOT'" not in block
    assert "'PRESSURE'" not in block
    assert "'TACKLE'" not in block
    assert "'INTERCEPTION'" not in block
    assert "'YELLOW'" not in block
    assert "'SUBSTITUTION'" not in block
    assert "'CORNER'" not in block


def test_live_goal_and_red_are_single_important_notifications():
    assert "'⚽ ГОЛ'" in LIVE
    assert "'🟥 КРАСНАЯ'" in LIVE
    assert "live-event:{e['id']}" in LIVE
    assert "🅰️ Ассист" in LIVE


def test_match_finished_notification_contains_goals_and_motm_and_report_key():
    start=SERVICES.index('async def _finish_live_match')
    end=SERVICES.index('async def advance_live_matches', start)
    block=SERVICES[start:end]
    assert "f\"⚽ <b>Голы:</b>" in block
    assert "f\"⭐ <b>MOTM:</b>" in block
    assert "match-finished:{match[\"id\"]}" in block
    assert "SELECT e.minute,e.club_id,e.player_id" in block


def test_finished_report_callback_is_registered_and_has_stats_lineups_ratings():
    start=BOT.index("@router.callback_query(F.data.startswith('match:watch:report:'))")
    end=BOT.index("@router.callback_query(F.data.startswith('match:watch:tactics:'", start)
    block=BOT[start:end]
    assert 'match_player_stats' in block
    assert 'match_lineups' in block
    assert '📊 <b>ПОЛНЫЙ ОТЧЁТ</b>' in block
    assert '👥 <b>Составы</b>' in block
    assert '⭐ <b>Рейтинги</b>' in block


def test_report_callback_is_excluded_from_generic_live_watch_handler():
    assert "@router.callback_query(F.data.regexp(r'^match:watch:\\d+$'))" in BOT
    assert "@router.callback_query(F.data.startswith('match:watch:report:'))" in BOT


def test_other_notifications_are_grouped_by_user_and_minute_but_priority_stays_separate():
    start=SERVICES.index('async def deliver_due_notifications')
    end=SERVICES.index('# --- Notifications', start)
    block=SERVICES[start:end]
    assert 'n.created_at' in block
    assert "created.replace(second=0,microsecond=0)" in block
    assert 'len(bucket_rows) < 3' in block
    assert "'match-start-10:'" in block
    assert "'match-started:'" in block
    assert "'match-finished:'" in block
    assert "'live-event:'" in block
    assert "<b>📋 Уведомления:</b>" in block
    assert "asyncio.gather(*(_send(items) for _,items in grouped))" in block


def test_notification_settings_are_still_checked_before_grouping():
    start=SERVICES.index('async def deliver_due_notifications')
    end=SERVICES.index('# --- Notifications', start)
    block=SERVICES[start:end]
    assert 'LEFT JOIN notification_settings ns' in block
    for col in ('match','injury','transfer','finance','development','discipline','morale','tournament'):
        assert f"WHEN '{col.upper() if col != 'match' else 'MATCH'}'" in block or col in block


def test_training_details_sorted_by_growth_points():
    start=SERVICES.index('async def training_details')
    end=SERVICES.index('async def set_player_expectation', start)
    block=SERVICES[start:end]
    assert 'ORDER BY l.points DESC,l.created_at,l.player_id' in block


def test_back_from_separate_live_message_deletes_viewer_when_possible():
    start=BOT.index("@router.callback_query(F.data.startswith('match:watch:back:'))")
    end=BOT.index("@router.callback_query(F.data=='table')", start)
    block=BOT[start:end]
    assert 'await cq.message.delete()' in block
