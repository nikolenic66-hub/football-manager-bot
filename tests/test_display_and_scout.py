from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_clean_display_name_removes_only_legacy_four_digit_suffix():
    from app.services import clean_display_name
    assert clean_display_name('Lucas Silva 0131') == 'Lucas Silva'
    assert clean_display_name('Kovacs 0350') == 'Kovacs'
    assert clean_display_name('Rossi 0287') == 'Rossi'
    assert clean_display_name('Leroy Sane') == 'Leroy Sane'
    assert clean_display_name('Player 12345') == 'Player 12345'


def test_player_name_property_also_cleans_legacy_suffix():
    from app.models import Player
    p = Player(1, 'Lucas', 'Silva 0131', 'ST', 25, 80, 80, 80, 80, 50, 70, 80, 80)
    assert p.name == 'Lucas Silva'


def test_player_display_surfaces_use_clean_display_name():
    bot = (ROOT / 'app' / 'bot.py').read_text(encoding='utf-8')
    render = (ROOT / 'app' / 'visual' / 'render.py').read_text(encoding='utf-8')
    admin = (ROOT / 'app' / 'handlers' / 'admin.py').read_text(encoding='utf-8')
    assert 'display_player_name' in bot
    assert 'clean_display_name' in bot
    assert 'clean_display_name' in render
    assert 'display_player_name' in admin
    assert '{r["first_name"]} {r["last_name"]}' not in bot
    assert "f\"{p.get('first_name','')} {p.get('last_name','')}\".strip()" not in render


def test_scout_position_filter_is_exact_database_position():
    bot = (ROOT / 'app' / 'bot.py').read_text(encoding='utf-8')
    start = bot.index('async def _scout_search')
    end = bot.index('\ndef _scout_results_keyboard', start)
    block = bot[start:end]
    assert "conditions.append('p.position = :pos')" in block
    assert "params['pos']=position" in block
    assert "p.position = ANY(:positions)" not in block
    for code in ('GK','LB','CB','RB','DM','CM','AM','LW','RW','ST'):
        assert f"('{code}'" in bot


def test_scout_position_keyboard_has_ten_positions_and_any():
    bot = (ROOT / 'app' / 'bot.py').read_text(encoding='utf-8')
    block = bot[bot.index('def _scout_position_keyboard'):bot.index('\ndef _scout_age_keyboard')]
    for label in ('🧤 ВРТ','🛡 ЛЗ','🛡 ЦЗ','🛡 ПЗ','⚙️ ОПЗ','⚙️ ЦПЗ','⚙️ АПЗ','⚡ ЛП','⚡ ПП','⚡ ЦФ'):
        assert label in block
    assert "'Любая'" in block
    assert "✓ ' if value==code" in block


def test_board_keeps_saved_lineup_order_as_slot_order():
    services = (ROOT / 'app' / 'services.py').read_text(encoding='utf-8')
    block = services[services.index('async def _club_board'):services.index('\nasync def set_player_instruction')]
    assert "SELECT player_ids FROM club_lineups WHERE club_id=:c" in block
    assert 'saved=[int(pid) for pid in saved]' in block
    assert 'for index,pid in enumerate(saved[:11])' in block
    assert 'board=board.assign(index,r[\'role\'],r[\'instruction\'])' in block
    assert 'build_starting_lineup' in services
    assert 'await _starting_players(s,club_id)' in block


def test_scout_back_callback_returns_to_results_with_current_page():
    from pathlib import Path
    source=Path('app/bot.py').read_text(encoding='utf-8')
    block=source[source.index("@router.callback_query(F.data.startswith('scout:back:'))"):source.index("@router.callback_query(F.data.startswith('scout:search:'))")]
    assert "return await _show_scout_results(c,c.from_user.id,max(0,page))" in block
    assert "logger.exception('Scout back navigation failed" in block
    assert "scout:back:{_scout_filter_state(c.from_user.id).get(\"page\",0)}" in source


def test_scout_back_uses_player_id_not_result_index():
    source=Path('app/bot.py').read_text(encoding='utf-8')
    assert "callback_data=f'scout:item:{r[\"id\"]}'" in source


def test_card_player_rounds_overall_to_integer():
    from pathlib import Path
    source=Path('app/bot.py').read_text(encoding='utf-8')
    block=source[source.index('def _card_player'):source.index('class LeagueCreate')]
    assert "int(round(float(data['overall'])))" in block
    assert "int(round(float(player_rating(pr))))" in block


def test_player_card_rounds_fallback_overall_to_integer():
    from app.visual.render import player_card
    source=Path('app/visual/render.py').read_text(encoding='utf-8')
    block=source[source.index('def player_card'):source.index('def formation_positions')]
    assert 'int(round(float(overall)))' in block
