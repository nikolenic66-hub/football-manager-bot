from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICES = (ROOT / 'app' / 'services.py').read_text()
MIGRATION = (ROOT / 'migrations' / '020_player_templates.sql').read_text()
MIGRATION_021 = (ROOT / 'migrations' / '021_full_reset_support.sql').read_text()
ADMIN = (ROOT / 'app' / 'handlers' / 'admin.py').read_text()


def test_create_player_copy_clones_template_and_marks_copy():
    block = SERVICES[SERVICES.index('async def create_player_copy'):SERVICES.index('async def club_has_template')]
    assert 'is_template=TRUE' in block
    assert 'is_template,template_id' in block
    assert 'FALSE,:t' in block
    assert 'portrait_url,portrait_source' in block
    assert 'NULL,NULL,talents' in block
    assert 'RETURNING id' in block
    assert "tactical_archetype,'copy',NULL,NULL,NULL,FALSE,:t" in block


def test_add_copy_to_club_compensates_duplicate_template_at_half_market_value():
    block = SERVICES[SERVICES.index('async def add_copy_to_club'):SERVICES.index('async def _template_usage_counts')]
    assert 'await club_has_template(s, club_id, template_id)' in block
    assert "comp=int(template['market_value'])//2" in block
    assert "UPDATE clubs SET budget=:b" in block
    assert "VALUES(:c,'OTHER',:amount,:balance,:description)" in block
    assert 'return None, comp' in block
    assert 'return int(inserted), 0' in block


def test_starting_lineup_has_23_slots_and_exact_rarity_targets_when_pool_is_sufficient():
    block = SERVICES[SERVICES.index('async def _build_starting_squad_from_templates'):SERVICES.index('def build_starting_lineup', SERVICES.index('async def _build_starting_squad_from_templates'))]
    assert "_TEMPLATE_SLOTS = [" in SERVICES
    from app.services import STARTING_SLOT_RARITIES
    assert len(STARTING_SLOT_RARITIES) == 23
    assert STARTING_SLOT_RARITIES.count('EPIC') == 2
    assert STARTING_SLOT_RARITIES.count('RARE') == 6
    assert STARTING_SLOT_RARITIES.count('BASE') == 15
    assert 'target_rarity=STARTING_SLOT_RARITIES[idx]' in block
    assert "('EPIC','RARE','BASE')" in block
    assert "('RARE','EPIC','BASE')" in block
    assert "('BASE','RARE','EPIC')" in block
    assert 'len(selected) != 23' in block
    assert 'len(set(selected)) != 23' in block


def test_template_migration_marks_1248_templates_and_removes_global_player_uniqueness():
    assert 'ADD COLUMN IF NOT EXISTS is_template BOOLEAN NOT NULL DEFAULT FALSE' in MIGRATION
    assert 'ADD COLUMN IF NOT EXISTS template_id BIGINT NULL REFERENCES players(id)' in MIGRATION
    assert 'UPDATE players' in MIGRATION and 'is_seed=TRUE' in MIGRATION and 'is_template=TRUE' in MIGRATION
    assert 'DROP INDEX IF EXISTS one_owner_per_player' in MIGRATION
    assert 'idx_players_template_rarity' in MIGRATION
    assert 'idx_players_template_id' in MIGRATION
    assert 'ADD COLUMN IF NOT EXISTS is_seed BOOLEAN NOT NULL DEFAULT FALSE' in MIGRATION
    assert "data_source IN ('seed','curated_real')" in MIGRATION
    assert "data_source IN ('generated','curated_real')" not in MIGRATION


def test_full_reset_clears_state_rebuilds_23_player_squads_and_admin_button_is_double_confirmed():
    reset = SERVICES[SERVICES.index('async def full_reset'):]
    for table in ('club_players','club_lineups','club_tactics','club_training','club_player_instructions',
                  'match_lineups','match_events','match_player_stats','match_substitutions','match_tactics',
                  'matches','league_teams','leagues','transfer_listings','transfer_offers','transfers',
                  'club_season_finances','club_financial_transactions'):
        assert f'DELETE FROM {table}' in reset
    assert 'UPDATE players SET is_template=TRUE,template_id=NULL WHERE is_seed=TRUE' in reset
    assert 'DELETE FROM players WHERE is_template=FALSE' in reset
    assert 'await build_starting_lineup(s,club_id)' in reset
    assert "VALUES(:u,'full_reset','system'" in reset
    assert "status='ACTIVE'" in reset
    assert "('🔄 СБРОСИТЬ ВСЁ С НУЛЯ','admin:system:reset')" in ADMIN
    assert "admin:system:reset:yes" in ADMIN
    assert "AdminStates.full_reset_confirm" in ADMIN


def test_full_reset_support_migration_never_executes_a_reset_at_deploy():
    assert 'CREATE INDEX IF NOT EXISTS idx_players_template_rarity_position' in MIGRATION_021
    assert 'DELETE FROM' not in MIGRATION_021
    assert 'UPDATE clubs' not in MIGRATION_021


def test_copy_ownership_column_and_unique_index_exist_in_new_migration():
    migration = (ROOT / 'migrations' / '022_club_player_templates.sql').read_text()
    assert 'ADD COLUMN IF NOT EXISTS template_id BIGINT' in migration
    assert 'UPDATE club_players cp' in migration
    assert 'CREATE UNIQUE INDEX IF NOT EXISTS one_copy_per_template_per_club' in migration
    assert 'WHERE template_id IS NOT NULL' in migration


def test_starting_lineup_persists_real_copy_ids():
    block = SERVICES[SERVICES.index('async def _build_starting_squad_from_templates'):SERVICES.index('def build_starting_lineup', SERVICES.index('async def _build_starting_squad_from_templates'))]
    assert 'created_ids=[]' in block
    assert 'new_id,_=await add_copy_to_club' in block
    assert "'ids':created_ids[:11]" in block
    assert 'len(created_ids) != 23' in block


def test_buy_and_transfer_reject_duplicate_templates():
    for marker in ('async def buy_player', 'async def accept_transfer_offer'):
        start = SERVICES.index(marker)
        end = SERVICES.find('\nasync def ', start + 10)
        block = SERVICES[start:end if end != -1 else None]
        assert 'COALESCE(p.template_id,p.id)' in block
        assert "У вас уже есть такой игрок." in block


def test_seed_and_admin_created_players_have_explicit_template_semantics():
    seed = (ROOT / 'app' / 'seed.py').read_text()
    admin = (ROOT / 'app' / 'handlers' / 'admin.py').read_text()
    assert 'is_seed,is_template,template_id' in seed
    assert ':data_source,TRUE,TRUE,NULL' in seed
    assert "data_source='curated_real' if real else 'seed'" in seed
    assert "'is_seed','is_template','template_id'" in admin
    assert "'admin'" in admin
    assert "'FALSE','TRUE','NULL'" in admin


def test_admin_template_delete_is_blocked_when_copies_exist():
    block = ADMIN[ADMIN.index('async def admin_player_delete'):ADMIN.index("@admin_router.callback_query(F.data == 'admin:player:create')")]
    assert 'SELECT COUNT(*) FROM players WHERE template_id=:id' in block
    assert 'У шаблона есть {copies} копий. Удалить нельзя.' in block


def test_seed_marker_repair_is_idempotent_and_preserves_admin_templates():
    migration = (ROOT / 'migrations' / '023_seed_marker_repair.sql').read_text()
    assert "data_source='admin'" in migration
    assert "data_source IN ('seed','curated_real')" in migration
    assert "CREATE INDEX IF NOT EXISTS idx_players_data_source" in migration
    assert migration.count('UPDATE players') >= 5
    assert 'admin_actions_log' in migration
    assert 'template_id=NULL' in migration


def test_migration_022_duplicate_cleanup_is_logged():
    db = (ROOT / 'app' / 'db.py').read_text()
    assert "migration.name == '022_club_player_templates.sql'" in db
    assert 'duplicate cleanup removed %s club_players rows.' in db
