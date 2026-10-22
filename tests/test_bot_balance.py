from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_bot_only_goal_multiplier_is_removed_from_live_engine():
    engine = (ROOT / 'app' / 'game' / 'engine.py').read_text(encoding='utf-8')
    assert '_ai_goal_multiplier' not in engine
    assert '1.40' not in engine
    assert 'segment_home *= _ai_goal_multiplier' not in engine
    assert 'segment_away *= _ai_goal_multiplier' not in engine


def test_bot_and_user_squads_share_the_same_template_rarity_profile():
    services = (ROOT / 'app' / 'services.py').read_text(encoding='utf-8')
    assert 'await ensure_squad(s,club_id)' in services
    assert 'await _starting_players(s,club_id)' in services
    assert 'STARTING_SLOT_RARITIES = tuple(' in services
    assert 'base_ovr + 10' not in services
