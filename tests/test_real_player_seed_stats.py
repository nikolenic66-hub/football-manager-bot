from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_fictional_stat_profiles_have_120_bounded_profiles():
    from app.real_player_stats import FICTIONAL_PLAYER_STATS

    assert len(FICTIONAL_PLAYER_STATS) == 120
    assert set(FICTIONAL_PLAYER_STATS) == set(range(1, 121))
    for profile in FICTIONAL_PLAYER_STATS.values():
        assert set(profile) == {'pace', 'shooting', 'passing', 'dribbling', 'defending', 'physical', 'stamina', 'mental'}
        assert all(52 <= value <= 94 for value in profile.values())


def test_fictional_player_catalog_contains_no_named_real_players():
    from app.player_catalog import REAL_ORDER

    from app.player_catalog import _FIRST, _LAST
    full_names = {f'{first} {last}'.strip().lower() for first, last, _, _ in REAL_ORDER}
    assert len(REAL_ORDER) == 120
    assert len(full_names) == 120
    assert all(first in _FIRST and last in _LAST for first, last, _, _ in REAL_ORDER)


def _load_seed_name_helpers():
    import ast

    source = (ROOT / 'app' / 'seed.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    names = {'TOTAL_PLAYERS', 'FIRST_NAMES', 'LAST_NAMES', '_synthetic_name_pool', '_synthetic_name'}
    nodes = []
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.FunctionDef)):
            targets = [node.name] if isinstance(node, ast.FunctionDef) else [target.id for target in node.targets if isinstance(target, ast.Name)]
            if any(name in names for name in targets):
                nodes.append(node)
    namespace = {}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(ROOT / 'app' / 'seed.py'), 'exec'), namespace)
    return namespace


def test_seed_generates_2880_unique_synthetic_names_without_premium_collisions():
    from random import Random
    from app.player_catalog import REAL_ORDER

    namespace = _load_seed_name_helpers()
    assert len(REAL_ORDER) == 120
    assert namespace['TOTAL_PLAYERS'] == 3000
    assert namespace['TOTAL_PLAYERS'] - len(REAL_ORDER) == 2880
    rng = Random(20261002)
    used = {(first.casefold(), last.casefold()) for first, last, _, _ in REAL_ORDER}
    premium = set(used)
    pool = namespace['_synthetic_name_pool'](rng)
    generated = [namespace['_synthetic_name'](pool, used) for _ in range(2880)]
    assert len(generated) == 2880
    assert len(set(generated)) == 2880
    assert len(used) == 120 + 2880
    assert not (premium & {(first.casefold(), last.casefold()) for first, last in generated})


def test_synthetic_name_pools_provide_more_than_5000_combinations():
    namespace = _load_seed_name_helpers()
    first_names = namespace['FIRST_NAMES']
    last_names = namespace['LAST_NAMES']
    assert len(first_names) >= 100
    assert len(last_names) >= 100
    assert len(set(first_names)) >= 100
    assert len(set(last_names)) >= 100
    assert len(set(first_names)) * len(set(last_names)) >= 2880 + 120


def test_legacy_extra_player_json_has_only_deterministic_fictional_names():
    import json
    from app.player_names import fictional_player_name

    payload = json.loads((ROOT / 'real_players_extra.json').read_text(encoding='utf-8'))
    names = [(row['first_name'], row['last_name']) for row in payload]
    expected = [fictional_player_name(i) for i in range(3001, 3001 + len(payload))]
    assert len(payload) == 480
    assert names == expected
    assert len(set(names)) == len(names)


def test_import_paths_do_not_mark_imported_players_as_real():
    importer = (ROOT / 'scripts' / 'import_players.py').read_text(encoding='utf-8')
    admin = (ROOT / 'app' / 'handlers' / 'admin.py').read_text(encoding='utf-8')
    assert 'VALUES(:fn,:ln,:nat,:age,:pos,:pace,:sho,:pas,:dri,:def,:phy,:sta,:men,:mv,:sal,:pot,:rar,:card,false' in importer
    assert 'fictional_first, fictional_last = fictional_player_name(row_index)' in importer
    assert 'first,last=fictional_player_name(row_index)' in admin
    assert "'synthetic_extra'" in admin


def test_legacy_extra_players_have_high_surname_diversity():
    import json
    payload = json.loads((ROOT / 'real_players_extra.json').read_text(encoding='utf-8'))
    surnames = {row['last_name'].casefold() for row in payload}
    full_names = {(row['first_name'].casefold(), row['last_name'].casefold()) for row in payload}
    assert len(full_names) == len(payload) == 480
    assert len(surnames) == 480
