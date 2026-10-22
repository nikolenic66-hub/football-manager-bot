from app.player_names import FIRST, LAST, fictional_player_name


def test_player_name_catalog_has_large_diverse_pools():
    assert len(FIRST) >= 300
    assert len(LAST) >= 1000
    names = [fictional_player_name(i) for i in range(3001, 3481)]
    assert len(set(names)) == 480
    assert len({last.casefold() for _, last in names}) == 480


def test_player_name_surnames_change_per_player_not_in_large_blocks():
    names = [fictional_player_name(i) for i in range(1, 101)]
    assert len({last.casefold() for _, last in names}) == len(names)
