from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_inventory_menu_and_player_card_actions_are_registered():
    bot = (ROOT / 'app' / 'bot.py').read_text(encoding='utf-8')
    assert "('🎒 Инвентарь','inventory:menu')" in bot
    assert "F.data=='inventory:menu'" in bot
    assert "F.data.startswith('inventory:use:')" in bot
    assert "F.data.startswith('inventory:apply:')" in bot
    assert '🎒 Использовать предмет' in bot
    assert 'Инвентарь пуст' in bot
    assert 'Ожидание старта сезона. Battle Pass активируется после начала первого матча в лиге.' in bot


def test_daily_quest_and_battle_pass_item_rewards_are_persisted():
    quests = (ROOT / 'app' / 'game' / 'daily_quests.py').read_text(encoding='utf-8')
    battle_pass = (ROOT / 'app' / 'game' / 'battle_pass.py').read_text(encoding='utf-8')
    assert 'INSERT INTO club_inventory(club_id,item_type,quantity)' in quests
    assert 'await add_inventory_item(session, int(club_id), reward[\'item\']' in battle_pass
    assert "elif reward['type'] == 'bundle':" in battle_pass
    assert 'STAMINA_BOOST' in battle_pass


def test_inventory_item_effects_and_three_match_morale_duration_are_wired():
    inventory = (ROOT / 'app' / 'game' / 'inventory.py').read_text(encoding='utf-8')
    services = (ROOT / 'app' / 'services.py').read_text(encoding='utf-8')
    migration = (ROOT / 'migrations' / '058_inventory_item_effects.sql').read_text(encoding='utf-8')
    assert 'is_injured=FALSE,injury_matches=0' in inventory
    assert 'morale_boost_matches=3' in inventory
    assert 'fitness=LEAST(100,fitness+30)' in inventory
    assert 'morale_boost_amount=LEAST(30,' in inventory
    assert 'advance_morale_boosts(s,[match[\'home_club_id\'],match[\'away_club_id\']])' in services
    assert 'morale_boost_matches' in migration and 'morale_boost_amount' in migration


def test_event_queue_uses_qualified_id_in_conflict_update():
    source = (ROOT / 'app' / 'game' / 'event_queue.py').read_text(encoding='utf-8')
    assert 'ON CONFLICT (idempotency_key) DO UPDATE SET id = season_events.id' in source
    assert 'CAST(:scheduled_at AS timestamptz)' in source


def test_battle_pass_admin_waits_for_an_active_league_season():
    admin = (ROOT / 'app' / 'handlers' / 'admin.py').read_text(encoding='utf-8')
    assert "SELECT id FROM seasons WHERE state='ACTIVE' ORDER BY id DESC LIMIT 1" in admin
    assert 'Ожидание старта сезона. Battle Pass активируется после начала первого матча в лиге.' in admin
    assert 'season=await start_season(s,days=31)' in admin


def test_team_inventory_actions_and_shortage_confirmation_are_registered():
    bot = (ROOT / 'app' / 'bot.py').read_text(encoding='utf-8')
    inventory = (ROOT / 'app' / 'game' / 'inventory.py').read_text(encoding='utf-8')
    assert "F.data.startswith('inventory:team:')" in bot
    assert "F.data.startswith('inventory:team_confirm:')" in bot
    assert 'use_inventory_item_for_team' in bot
    assert 'team_item_status' in inventory
    assert "min(status['needed'], status['available'])" in inventory


def test_daily_fitness_recovery_is_idempotent_and_has_schema_marker():
    fitness = (ROOT / 'app' / 'game' / 'fitness.py').read_text(encoding='utf-8')
    migration = (ROOT / 'migrations' / '061_daily_fitness_recovery.sql').read_text(encoding='utf-8')
    worker = (ROOT / 'app' / 'live_worker.py').read_text(encoding='utf-8')
    assert 'fitness_recovered_on=CURRENT_DATE - 1' in fitness
    assert 'fitness_recovered_on < CURRENT_DATE - 1' in fitness
    assert 'THEN 15 ELSE 20 END' in fitness
    assert 'fitness_recovered_on' in migration
    assert 'recover_daily_fitness(s)' in worker
