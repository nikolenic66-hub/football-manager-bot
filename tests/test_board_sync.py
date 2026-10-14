from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _block(source, start_marker, end_marker):
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    return source[start:end]


def test_starting_players_persist_autosquad_when_lineup_is_missing():
    source = (ROOT / 'app' / 'services.py').read_text(encoding='utf-8')
    block = _block(source, 'async def _starting_players', 'async def _bench_players')
    assert "SELECT player_ids FROM club_lineups WHERE club_id=:c" in block
    assert 'build_starting_lineup(list(rows_by_id.values()), slots)' in block
    assert "INSERT INTO club_lineups(club_id,player_ids)" in block
    assert "ON CONFLICT(club_id) DO UPDATE SET player_ids=EXCLUDED.player_ids" in block


def test_club_board_uses_saved_player_ids_as_formation_slots():
    source = (ROOT / 'app' / 'services.py').read_text(encoding='utf-8')
    block = _block(source, 'async def _club_board', 'async def set_player_instruction')
    assert "SELECT player_ids FROM club_lineups WHERE club_id=:c" in block
    assert 'for index,pid in enumerate(saved[:11])' in block
    assert 'players=[by_id[pid] for pid in saved]' in block
    assert "board=board.assign(index,r['role'],r['instruction'])" in block
    assert 'if p is None:' in block


def test_tactical_board_query_is_limited_to_saved_starting_xi_and_exposes_slots():
    source = (ROOT / 'app' / 'services.py').read_text(encoding='utf-8')
    block = _block(source, 'async def get_tactical_board', 'async def _ensure_same_league')
    assert 'players=await _starting_players(s,club_id)' in block
    assert 'WHERE cp.club_id=:c AND p.id=ANY(:ids)' in block
    assert "r['slot_position']=slot" in block
    assert "r['slot_label']=POSITION_LABELS_RU.get(slot,slot)+suffix" in block
    assert "r['board_x']=x if r.get('board_x') is None else r['board_x']" in block
    assert "r['board_y']=y if r.get('board_y') is None else r['board_y']" in block


def test_tactical_board_renderer_uses_slot_labels_and_shirt_numbers():
    source = (ROOT / 'app' / 'visual' / 'render.py').read_text(encoding='utf-8')
    block = _block(source, 'def tactical_board_screen', 'def squad_screen')
    assert "p.get('slot_label',p.get('position',''))" in block
    assert "p.get('shirt_number') or i+1" in block
    assert "p.get('board_x'); by=p.get('board_y')" in block


def test_club_board_repairs_stale_saved_ids_instead_of_reordering_squad():
    source = (ROOT / 'app' / 'services.py').read_text(encoding='utf-8')
    block = _block(source, 'async def _club_board', 'async def set_player_instruction')
    assert 'WHERE cp.club_id=:c AND p.id=ANY(:ids)' in block
    assert 'if len(by_id) != 11' in block
    assert 'players=await _starting_players(s,club_id)' in block
    assert 'players=[by_id[pid] for pid in saved]' in block


def test_club_board_never_replaces_saved_order_with_ovr_sorting():
    source = (ROOT / 'app' / 'services.py').read_text(encoding='utf-8')
    block = _block(source, 'async def _club_board', 'async def set_player_instruction')
    assert 'for index,pid in enumerate(saved[:11])' in block
    assert 'ORDER BY p.potential DESC' not in block


def test_display_player_name_cleans_suffix_from_each_name_component():
    from app.services import display_player_name
    assert display_player_name('Lucas 0131', 'Silva 0350') == 'Lucas Silva'
    assert display_player_name('Lucas', 'Silva 0131') == 'Lucas Silva'
    assert display_player_name('Lucas 0131', '') == 'Lucas'
