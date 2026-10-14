from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
BOT=(ROOT/'app'/'bot.py').read_text(encoding='utf-8')
SERVICES=(ROOT/'app'/'services.py').read_text(encoding='utf-8')


def _block(name, next_name=None):
    start=BOT.index(name)
    end=BOT.index(next_name,start) if next_name else len(BOT)
    return BOT[start:end]


def test_live_watch_callbacks_and_keyboard_are_present():
    block=_block("_LIVE_SCREENS: dict[int, asyncio.Task]", "@router.callback_query(F.data=='table')")
    assert "match:watch:tactics:{match_id}" in block
    assert "match:watch:refresh:{match_id}" in block
    assert "match:watch:lineups:{match_id}" in block
    assert "match:watch:stats:{match_id}" in block
    assert "match:watch:back:{match_id}" in block


def test_live_screen_auto_refresh_is_ten_seconds_and_cancellable():
    block=_block("async def _live_screen_loop", "async def _open_live_screen")
    assert "await asyncio.sleep(10)" in block
    assert "except asyncio.CancelledError" in block
    assert "_LIVE_SCREENS.pop(user_id,None)" in block


def test_live_screen_does_not_edit_when_signature_is_unchanged():
    block=_block("async def _render_live_screen", "async def _live_screen_loop")
    assert "if not force and _LIVE_SCREEN_SIGNATURES.get(key)==signature" in block
    assert "_edit_live_message" in block


def test_live_screen_finished_report_contains_motm_and_finished_header():
    block=_block("def _live_watch_text", "async def _edit_live_message")
    assert "МАТЧ ЗАВЕРШЁН" in block
    assert "ЛУЧШИЙ ИГРОК (MOTM)" in block
    assert "Ключевые события" in block


def test_live_screen_lineups_uses_match_lineups_and_player_rating():
    block=_block("async def cb_match_watch_lineups", "@router.callback_query(F.data.startswith('match:watch:stats:'))")
    assert "FROM match_lineups ml JOIN players p" in block
    assert "ml.is_starting=true" in block
    assert "player_rating(pr)" in block
    assert "fitness" in block


def test_live_screen_stats_exposes_required_match_statistics():
    block=_block("async def cb_match_watch_stats", "@router.callback_query(F.data.startswith('match:watch:tactics:') | F.data.startswith('match:watch:sub:'))")
    for field in ('home_possession','away_possession','home_shots','away_shots','home_shots_on_target','away_shots_on_target','home_corners','away_corners','home_xg','away_xg'):
        assert field in block
    assert 'YELLOW' in block
    assert 'RED' in block
    assert 'FOUL' in block
    assert 'match_player_stats' in block


def test_match_center_has_watch_button_for_every_live_match():
    block=_block("async def cmd_matchcenter", "@router.message(Command('cards'))")
    assert "WHERE m.status='LIVE'" in block
    assert "match:watch:{live[\"id\"]}" in block
    assert '📺 Смотреть матч' in block


def test_match_start_notifications_include_watch_button_data():
    assert "match-start-10:{league[\"id\"]}:{f.round_no}:{cid}:{match_id}" in SERVICES
    assert "match-started:{m[\"id\"]}" in SERVICES
    delivery=SERVICES[SERVICES.index('async def deliver_due_notifications'):SERVICES.index('# --- Notifications', SERVICES.index('async def deliver_due_notifications'))]
    assert "InlineKeyboardButton(text='📺 Смотреть матч'" in delivery
    assert "callback_data=f'match:watch:{mid}'" in delivery


def test_back_cancels_live_task_and_logs_close():
    block=_block("async def cb_match_watch_back", "@router.callback_query(F.data=='table')")
    assert "await _cancel_live_screen(cq.from_user.id)" in block
    assert 'Live screen closed by user' in block


def test_live_goal_events_use_score_at_event_not_current_score():
    block=_block("async def _load_live_view", "def _live_event_lines")
    assert "_score_at_event" in block
    assert "_live_score_at_minute" in block
    lines=_block("def _live_event_lines", "def _live_watch_text")
    assert "event_score=e.get('_score_at_event')" in lines
    assert "event_score[0]}:{event_score[1]}" in lines


def test_live_watch_opens_a_separate_message_and_keeps_match_center_intact():
    block=_block("async def _open_live_screen", "@router.callback_query(F.data.regexp(r'^match:watch:\\d+$'))")
    assert "live_message=await cq.bot.send_message" in block
    assert "_edit_live_message(cq.message" not in block
    assert "_live_screen_loop(live_message" in block


def test_live_watch_initial_signature_matches_render_signature():
    block=_block("async def _open_live_screen", "@router.callback_query(F.data.regexp(r'^match:watch:\\d+$'))")
    render=_block("async def _render_live_screen", "async def _live_screen_loop")
    signature_fragment="[(e['minute'],e['event_type'],e['description'],e.get('_score_at_event')) for e in events]"
    assert signature_fragment in block
    assert signature_fragment in render


def test_live_watch_callback_matches_only_numeric_match_ids():
    bot = BOT
    assert "@router.callback_query(F.data.regexp(r'^match:watch:\\d+$'))" in bot


def test_live_watch_uses_direct_bot_send_message_and_acknowledges_callback_first():
    bot = BOT
    start = bot.index("@router.callback_query(F.data.regexp(r'^match:watch:")
    end = bot.index("@router.callback_query(F.data.startswith('match:watch:refresh:'))", start)
    block = bot[start:end]
    assert "await cq.answer('📺 Открываю live-экран…')" in block
    open_start = bot.index("async def _open_live_screen")
    open_end = bot.index("@router.callback_query(F.data.regexp(r'^match:watch:", open_start)
    open_block = bot[open_start:open_end]
    assert "await cq.bot.send_message(" in open_block
    assert "cq.message.answer(" not in open_block
