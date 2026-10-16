from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / 'app' / 'bot.py').read_text()
SERVICES = (ROOT / 'app' / 'services.py').read_text()
MODELS = (ROOT / 'app' / 'models.py').read_text()
ADMIN = (ROOT / 'app' / 'handlers' / 'admin.py').read_text()
MIGRATION = (ROOT / 'migrations' / '018_fix_empty_player_names.sql').read_text()
MIGRATION_019 = (ROOT / 'migrations' / '019_repair_player_sequence_and_real_names.sql').read_text()
SEED = (ROOT / 'app' / 'seed.py').read_text()
LIVE_WORKER = (ROOT / 'app' / 'live_worker.py').read_text()
ENGINE = (ROOT / 'app' / 'game' / 'engine.py').read_text()


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


def test_admin_player_create_can_be_cancelled_and_regular_start_cancel_own_fsm():
    assert "admin:player:create:cancel" in ADMIN
    assert "async def admin_player_create_cancel" in ADMIN
    assert "async def admin_start_reset" not in ADMIN
    assert "async def admin_cancel" not in ADMIN
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


def test_seed_repairs_players_sequence_and_serializes_bootstrap():
    assert "pg_advisory_xact_lock(hashtext('football-manager-seed'))" in SEED
    assert "setval('players_id_seq'" in SEED
    assert 'INSERT INTO players(id,' in SEED


def test_second_half_xg_uses_first_half_as_base_not_full_match_xg():
    block = SERVICES[SERVICES.index('async def advance_live_matches'):SERVICES.index('# --- Club management expansion', SERVICES.index('async def advance_live_matches'))]
    assert "phase_data.get('first_half_xg')" in block
    assert "phase_data.get('full_xg') or" not in block
    # The persisted phase still records the mathematical full-match total once.
    assert "data['full_xg']=[round(first_half_xg[0]+hx*.52,2)" in SERVICES


def test_admin_router_does_not_capture_start_or_cancel():
    assert "@admin_router.message(CommandStart())" not in ADMIN
    assert "@admin_router.message(Command('cancel'))" not in ADMIN
    assert "@router.message(CommandStart())" in BOT
    assert "@router.message(Command('cancel'))" in BOT


def test_live_goal_red_notifications_skip_negative_telegram_owners():
    assert 'JOIN users u ON u.id=c.owner_user_id' in LIVE_WORKER
    assert "if int(telegram_id) < 0:" in LIVE_WORKER
    assert 'await queue_notification(s,uid,category,title,body,dedupe_key)' in LIVE_WORKER


def test_match_events_carry_scorer_and_assist_ids():
    assert 'player_id:int|None=None; secondary_player_id:int|None=None' in ENGINE
    assert "{},p.id,a.id if a else None" in ENGINE
    assert "{},scorer.id,assist.id if assist else None" in ENGINE
    assert "pid=int(ev.player_id) if ev.player_id is not None else None" in SERVICES
    assert "sid=int(ev.secondary_player_id) if ev.secondary_player_id is not None else None" in SERVICES


def test_live_screen_has_concurrency_lock():
    assert '_LIVE_SCREEN_LOCK = asyncio.Lock()' in BOT
    assert 'async with _LIVE_SCREEN_LOCK:' in BOT
    assert 'await _cancel_live_screen_unlocked(cq.from_user.id)' in BOT


def test_seed_migration_sequence_repair_is_single_statement():
    assert "SELECT setval(" in MIGRATION_019
    assert 'players_id_seq' in MIGRATION_019
