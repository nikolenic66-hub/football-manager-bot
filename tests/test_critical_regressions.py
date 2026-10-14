from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / 'app' / 'bot.py').read_text()
SERVICES = (ROOT / 'app' / 'services.py').read_text()
MODELS = (ROOT / 'app' / 'models.py').read_text()
ADMIN = (ROOT / 'app' / 'handlers' / 'admin.py').read_text()
MIGRATION = (ROOT / 'migrations' / '018_fix_empty_player_names.sql').read_text()


def _callback_handlers(source, prefix):
    tree = ast.parse(source)
    found = []
    for node in tree.body:
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            decorators = []
            for dec in node.decorator_list:
                text = ast.get_source_segment(source, dec) or ''
                decorators.append(text)
            if any(prefix in d for d in decorators):
                found.append(node.name)
    return found


def test_tactic_item_has_exactly_one_handler_and_uses_player_id():
    assert _callback_handlers(BOT, "startswith('tactic:item:')") == ['cb_tactic_item']
    assert "callback_data=f'tactic:item:{pid}:{filter_code}:{page}'" in BOT
    start = BOT.index("@router.callback_query(F.data.startswith('tactic:item:'))")
    block = BOT[start:BOT.index("@router.callback_query(F.data=='visual:squad')", start)]
    assert "pid=int(parts[2])" in block
    assert "WHERE cp.club_id=:c AND p.id=:pid" in block
    assert 'page_rows=' not in block
    assert 'Устаревшая кнопка. Откройте Тактику заново.' in block


def test_tactic_player_picker_and_analogous_player_callbacks_use_ids():
    assert "callback_data=f'tactic:player:{pid}'" in BOT
    squad_block = BOT[BOT.index('def squad_keyboard'):BOT.index('def tactic_keyboard')]
    assert "callback_data=f'{prefix}:item:{int(p.get(\"id\", 0))}'" in squad_block
    assert "callback_data=f'market:item:{int(r[\"id\"])}'" in BOT
    assert "callback_data=f'contract:item:{int(r[\"id\"])}'" in BOT
    assert "callback_data=f'scout:item:{r[\"id\"]}'" in BOT
    for prefix in ('squad:item:', 'market:item:', 'contract:item:', 'scout:item:'):
        assert _callback_handlers(BOT, f"startswith('{prefix}')")


def test_tactic_item_rejects_legacy_index_callback_shape():
    start = BOT.index("@router.callback_query(F.data.startswith('tactic:item:'))")
    block = BOT[start:BOT.index("@router.callback_query(F.data=='visual:squad')", start)]
    assert 'if len(parts)!=5:' in block
    assert 'Устаревшая кнопка. Откройте Тактику заново.' in block


def test_player_display_fields_are_part_of_shared_player_fields_and_fallbacks_are_defensive():
    assert 'p.id,p.first_name,p.last_name,p.position' in SERVICES
    assert "return 'Player'" in SERVICES
    assert "re.fullmatch(r'\\d{4}', value)" in SERVICES
    assert "return cleaned or f'Игрок {self.id}'" in MODELS
    assert "mappings().all()" in BOT[BOT.index('async def _load_squad_rows'):BOT.index('async def _show_squad')]


def test_empty_player_name_migration_repairs_both_empty_and_numeric_only_names():
    assert 'UPDATE players' in MIGRATION
    assert "btrim(COALESCE(first_name, '')) = ''" in MIGRATION
    assert "btrim(COALESCE(last_name, '')) = ''" in MIGRATION
    assert "~ '^[0-9]{4}$'" in MIGRATION
    assert "THEN 'Player'" in MIGRATION
    assert "THEN id::text" in MIGRATION


def test_admin_player_create_has_schema_aware_insert_and_logs_exception():
    start = ADMIN.index('async def apc_pot')
    end = ADMIN.index('# ---------------- clubs ----------------', start)
    block = ADMIN[start:end]
    assert 'information_schema.columns' in block
    assert "'overall' in column_names" in block
    assert 'required_without_default' in block
    assert 'market_value' in block and 'salary' in block and 'potential' in block
    assert "logger.exception('Admin player create failed')" in block
    assert 'await state.clear()' in block
    assert 'CAST(:p AS jsonb)' in block


def test_admin_player_create_can_be_cancelled_and_admin_start_cancel_clear_fsm():
    assert "admin:player:create:cancel" in ADMIN
    assert "async def admin_player_create_cancel" in ADMIN
    assert "async def admin_start_reset" in ADMIN
    assert "async def admin_cancel" in ADMIN
    assert "async def start(m:Message, state:FSMContext):" in BOT
    start = BOT.index('async def start(m:Message, state:FSMContext):')
    assert 'await state.clear()' in BOT[start:BOT.index('@router.message(Command(\'createleague\'))', start)]


def test_all_player_creation_prompts_expose_cancel_keyboard():
    start = ADMIN.index('@admin_router.callback_query(F.data == \'admin:player:create\')')
    end = ADMIN.index('# ---------------- clubs ----------------', start)
    block = ADMIN[start:end]
    assert block.count('player_create_cancel_keyboard()') >= 7


def test_display_name_cleaning_handles_legacy_suffix_without_generic_player_label():
    from app.services import clean_display_name, display_player_name

    assert clean_display_name('Иванов') == 'Иванов'
    assert clean_display_name('Иванов 0131') == 'Иванов'
    assert clean_display_name('0131') == ''
    assert display_player_name('Иванов 0131', '') == 'Иванов'
    assert display_player_name('', '') == 'Player'
    assert display_player_name('0131', '') == 'Игрок 0131'
