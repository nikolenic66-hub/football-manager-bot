from pathlib import Path

ROOT = Path(__file__).parents[1]
SERVER = (ROOT / "app" / "server.py").read_text(encoding="utf-8")
SERVICES = (ROOT / "app" / "services.py").read_text(encoding="utf-8")
PACKS = (ROOT / "app" / "game" / "packs.py").read_text(encoding="utf-8")
API = (ROOT / "mini_app" / "src" / "api.ts").read_text(encoding="utf-8")
UI = (ROOT / "mini_app" / "src" / "ClubScreen.tsx").read_text(encoding="utf-8")
CSS = (ROOT / "mini_app" / "src" / "style.css").read_text(encoding="utf-8")


def test_pack_open_returns_card_fields_and_real_rarity():
    assert "'rarity': str(template['rarity'] or 'BASE').upper()" in PACKS
    assert "'portrait_url': template['portrait_url']" in PACKS
    assert "'potential': int(template['potential'] or _template_ovr(template))" in PACKS
    assert "@app.post(\"/api/packs/open\")" in SERVER


def test_pack_ui_has_animated_reveal_and_rarity_classes():
    assert "pack-opening-overlay" in UI
    assert "player-card " in UI
    assert "rarityClass(lastPackResult.rarity)" in UI
    assert "@keyframes cardReveal" in CSS
    assert "rarity-legendary" in CSS and "rarity-epic" in CSS and "rarity-rare" in CSS


def test_lineup_uses_server_formation_coordinates_and_slot_order():
    assert '"formation_slots": [' in SERVER
    assert "ordered = get_best_xi([dict(r) for r in rows], formation)" in SERVER
    assert "style={{left:`${slot.x}%`,top:`${100-slot.y}%`}}" in UI
    assert "if(preserveLineupSelection.current){preserveLineupSelection.current=false;}else{setLineupSlots(r.formation_slots||[])" in UI
    assert "export type FormationSlot" in API


def test_friendly_and_test_match_share_authorized_simulation_endpoint():
    assert '@app.post("/api/matches/friendly")' in SERVER
    assert '@app.post("/api/matches/test")' in SERVER
    assert "create_user_friendly_match(session, int(club[\"id\"]), mode=payload.mode)" in SERVER
    start = SERVICES.index("async def create_user_friendly_match")
    end = SERVICES.index("async def create_admin_test_match", start)
    block = SERVICES[start:end]
    # User-triggered friendly/test matches run through the paced LIVE engine,
    # not the old instantaneous full-match simulator.
    assert "simulate_first_half(" in block
    assert "status='LIVE'" in block
    assert "league_id,season_id,round,home_club_id,away_club_id" in block
    assert "status='FINISHED'" in block  # idempotent reuse also recognises recently finished tests
    assert "league_id=NULL" in block or "VALUES(NULL,NULL,0" in block
    assert "🤝 Товарищеский матч" in UI
    assert "🧪 Запустить тестовый матч" in UI


def test_calendar_last_result_and_prematch_bench_are_connected():
    assert '@app.get("/api/matches/last")' in SERVER
    assert "getLastMatch" in API and "lastMatch.home_score" in UI
    assert "'CHAMPIONS_LEAGUE','UCL'" in SERVER  # legacy tournaments are explicitly filtered out
    fixtures_endpoint = SERVER.split('async def league_fixtures', 1)[1].split('@app.get("/api/training/today")', 1)[0]
    assert "'LEAGUE' AS competition_type" in fixtures_endpoint
    assert "FROM cup_matches cm JOIN cups cup ON cup.id=cm.cup_id" not in fixtures_endpoint
    assert "FROM champions_matches clm JOIN champions_league cl ON cl.id=clm.cl_id" not in fixtures_endpoint
    assert "f.competition_type??f.comp_type??'LEAGUE'" in UI
    assert "competitionFilter" not in UI
    assert "prematch-bench-picker" in UI
    assert "saveStartingLineup(startingIds,lineupFormation,benchIds)" in UI
    assert "background-image:url('/stadium.jpg')" in CSS
    for rarity in ('base', 'rare', 'gold', 'epic', 'legendary'):
        assert f"url('/cards/{rarity}.png')" in CSS


def test_bench_migration_repairs_previously_marked_but_missing_column():
    db = (ROOT / "app" / "db.py").read_text(encoding="utf-8")
    migration = (ROOT / "migrations" / "068_bench_ids_schema_repair.sql").read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS bench_ids BIGINT[]" in db
    assert "ADD COLUMN IF NOT EXISTS bench_ids BIGINT[]" in migration


def test_live_match_actions_are_authenticated_and_persisted():
    assert '@app.post("/api/matches/{match_id}/substitute")' in SERVER
    assert '@app.post("/api/matches/{match_id}/tactics")' in SERVER
    assert "INSERT INTO match_substitutions" in SERVER
    assert "INSERT INTO match_events" in SERVER
    assert "getLiveMatchLineup" in API
    assert "substituteLivePlayer" in UI and "updateLiveMatchTactics" in UI
    assert "setTeams(matchRef.current.home_club_id, matchRef.current.away_club_id)" in (ROOT / "mini_app" / "src" / "components" / "MatchCanvas.tsx").read_text(encoding="utf-8")


def test_division_tier_is_returned_and_rendered():
    assert 'division_name(division["tier"]) if division else None' in SERVER
    assert 'division_name(row.tier)' in SERVER
    assert "league?.division_name||club.division_name" in UI


def test_match_event_feed_is_newest_first():
    assert ".sort((a,b)=>b.minute-a.minute||b.id-a.id)" in UI


def test_live_tactics_are_carried_into_both_live_timeline_and_second_half():
    assert 'UPDATE matches SET {second_column}=CAST(:data AS jsonb)' in SERVER
    assert '"type": "TACTICAL_CHANGE"' in SERVER
    assert '"new_sliders"' in SERVER


def test_next_match_uses_normalized_competition_names():
    assert "CASE UPPER(COALESCE(m.competition_type,''))" in SERVER
    assert "THEN 'Тестовый матч' ELSE 'Товарищеский матч' END" in SERVER


def test_tournaments_are_disabled_and_live_commentator_remains_available():
    fixtures_endpoint = SERVER.split('async def league_fixtures', 1)[1].split('@app.get("/api/training/today")', 1)[0]
    assert "'LEAGUE' AS competition_type" in fixtures_endpoint
    assert "FROM cup_matches cm JOIN cups cup ON cup.id=cm.cup_id" not in fixtures_endpoint
    assert "FROM champions_matches clm JOIN champions_league cl ON cl.id=clm.cl_id" not in fixtures_endpoint
    bot = (ROOT / "app" / "bot.py").read_text(encoding="utf-8")
    admin = (ROOT / "app" / "handlers" / "admin.py").read_text(encoding="utf-8")
    assert 'start_cup_for_league' not in bot and 'start_champions_league' not in bot
    assert 'start_cup_for_league' not in admin and 'start_champions_league' not in admin
    assert "league:cup'" in bot and "league:champions'" in bot  # stale buttons show a disabled notice
    assert 'Исторические записи старых турниров сохранены' in admin
    assert "live-commentator-heading" in UI
    assert "Игра идет, ждем опасного момента" in UI
    scene = (ROOT / "mini_app" / "src" / "game" / "MatchScene.ts").read_text(encoding="utf-8")
    assert "update(_time: number, delta: number)" in scene
    assert "duration: 2000" in scene
    assert "const isPass = kind.includes('PASS') || kind.includes('CROSS')" in scene
