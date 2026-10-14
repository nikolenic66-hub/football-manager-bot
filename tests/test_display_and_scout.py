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
