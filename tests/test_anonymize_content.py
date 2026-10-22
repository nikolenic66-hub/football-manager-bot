from scripts.anonymize_content import fictional_player_name
from app.player_catalog import REAL_ORDER, _FIRST, _LAST


def test_anonymized_names_are_stable_and_unique_for_large_roster():
    names = [fictional_player_name(player_id) for player_id in range(1, 10001)]
    assert len(set(names)) == 10000
    assert fictional_player_name(417) == fictional_player_name(417)
    assert all(first in _FIRST and last.split('-')[0] in _LAST for first, last in names[:1200])


def test_seed_catalog_is_fictional_and_balanced_across_positions():
    assert len(REAL_ORDER) == 120
    assert len({(first, last) for first, last, _, _ in REAL_ORDER}) == 120
    assert {position for _, _, _, position in REAL_ORDER} == {'GK', 'CB', 'LB', 'RB', 'DM', 'CM', 'AM', 'LW', 'RW', 'ST'}
