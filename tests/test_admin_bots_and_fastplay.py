from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICES = (ROOT / 'app' / 'services.py').read_text()
ADMIN = (ROOT / 'app' / 'handlers' / 'admin.py').read_text()


def test_test_bot_creation_uses_negative_owner_and_23_player_squad_setup():
    block = SERVICES[SERVICES.index('async def create_test_bot_club'):SERVICES.index('async def create_league')]
    assert 'SELECT MIN(telegram_id) FROM users WHERE telegram_id<0' in block
    assert "VALUES(:tg,:username,:first_name) RETURNING id" in block
    assert "'username':f'bot_{telegram_id}'" in block
    assert "'first_name':f'{name} Bot'" in block
    assert 'budget,fans,reputation,ticket_price' in block
    assert 'await ensure_squad(s,club_id)' in block
    assert 'await _starting_players(s,club_id)' in block
    assert 'INSERT INTO club_training' in block
    assert 'INSERT INTO club_tactics' in block
    assert 'test_bot_club_create' in block


def test_admin_club_menu_has_all_requested_bot_and_list_actions():
    assert "'🤖 Создать тестовый клуб','admin:club:create_bot'" in ADMIN
    assert "'📋 Все клубы','admin:clubs:list:0'" in ADMIN
    assert "'🤖 Список тестовых клубов','admin:clubs:bots:0'" in ADMIN
    assert "u.telegram_id<0" in ADMIN
    assert 'admin:clubs:list:' in ADMIN
    assert 'admin:clubs:bots:' in ADMIN


def test_admin_club_card_marks_negative_owner_as_test_bot():
    start = ADMIN.index('async def admin_club_router')
    block = ADMIN[start:ADMIN.index('# ---------------- finance ----------------', start)]
    assert "int(r['telegram_id'])<0" in block
    assert '🤖 <b>ТЕСТОВЫЙ КЛУБ</b>' in block


def test_league_picker_uses_free_and_current_members_queries_with_pagination():
    start = ADMIN.index('async def _admin_league_club_picker')
    block = ADMIN[start:ADMIN.index('async def _admin_league_table', start)]
    assert 'NOT EXISTS (SELECT 1 FROM league_teams' in block
    assert 'JOIN league_teams lt ON lt.club_id=c.id' in block
    assert 'LIMIT 10 OFFSET :off' in block
    assert 'admin:league:{lid}:addclub:' in block
    assert 'admin:league:{lid}:removeclub:' in block


def test_fast_round_persists_events_and_finishes_matches_without_live_notifications():
    start = SERVICES.index('async def _persist_fast_match_result')
    end = SERVICES.index('async def finalize_live_round', start)
    block = SERVICES[start:end]
    assert 'INSERT INTO match_events' in block
    assert 'INSERT INTO match_player_stats' in block
    assert "status='FINISHED'" in block
    assert 'simulate_match(' in block
    assert 'await finalize_live_round(s,league_id,results)' in block
    assert 'queue_club_notification' not in block


def test_fast_season_loops_until_league_is_finished():
    start = SERVICES.index('async def fast_play_all')
    block = SERVICES[start:SERVICES.index('async def finalize_live_round', start)]
    assert 'while True:' in block
    assert 'await fast_play_round(s,league_id)' in block
    assert 'Нет оставшихся туров.' in block


def test_admin_league_card_exposes_fast_round_and_fast_all_buttons():
    start = ADMIN.index('async def admin_league_router')
    block = ADMIN[start:ADMIN.index('# Legacy FSM compatibility marker', start)]
    assert "admin:league:{lid}:fastround" in block
    assert "admin:league:{lid}:fastall" in block
    assert "action=='fastround'" in block
    assert "action=='fastall'" in block


def test_bot_club_notifications_are_suppressed_before_queueing():
    start = SERVICES.index('async def queue_club_notification')
    block = SERVICES[start:SERVICES.index('async def notification_list', start)]
    assert 'JOIN users u ON u.id=c.owner_user_id' in block
    assert "int(owner['telegram_id']) < 0" in block
    assert 'return None' in block
