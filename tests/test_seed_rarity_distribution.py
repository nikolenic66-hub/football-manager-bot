import ast
import json
from collections import Counter
from pathlib import Path
from random import Random

from sqlalchemy import text
from app.player_catalog import REAL_ORDER

ROOT = Path(__file__).resolve().parents[1]


def _seed_namespace():
    source_path = ROOT / 'app' / 'seed.py'
    tree = ast.parse(source_path.read_text(encoding='utf-8'))
    nodes = [node for node in tree.body if isinstance(
        node, (ast.Assign, ast.AnnAssign, ast.FunctionDef, ast.AsyncFunctionDef)
    )]
    namespace = {'Random': Random, 'json': json, 'text': text, 'REAL_ORDER': REAL_ORDER}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source_path), 'exec'), namespace)
    return namespace


def test_seed_generates_3000_players_with_target_rarity_distribution():
    namespace = _seed_namespace()
    rows = namespace['_build_seed_rows'](Random(20261002), namespace['TOTAL_PLAYERS'])

    assert len(rows) == 3000
    assert Counter(row['rarity'] for row in rows) == Counter({
        'BASE': 2080,
        'RARE': 500,
        'EPIC': 300,
        'LEGENDARY': 120,
    })
    assert [row['rarity'] for row in rows[:120]] == (
        ['LEGENDARY'] * 30 + ['EPIC'] * 80 + ['RARE'] * 10
    )
    assert [row['rarity'] for row in rows[120:360]] == ['RARE'] * 240
    assert [row['rarity'] for row in rows[360:1248]] == ['BASE'] * 888
    names = [(row['first_name'].casefold(), row['last_name'].casefold()) for row in rows]
    assert len(set(names)) == 3000
    assert len({row['id'] for row in rows}) == 3000
