import ast
import asyncio
import json
import os
from pathlib import Path
from random import Random

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]


class _Result:
    def __init__(self, value=0):
        self.value = value

    def scalar_one(self):
        return self.value


class _Session:
    def __init__(self, count=0):
        self.count = count
        self.sql = []
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.sql.append(sql)
        if 'SELECT count(*) FROM players' in sql:
            return _Result(self.count)
        return _Result(0)

    async def commit(self):
        self.commits += 1


class _SessionFactory:
    def __init__(self, session):
        self.session = session

    def __call__(self):
        return self.session


def _load_seed():
    source = (ROOT / 'app' / 'seed.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    nodes = [
        node for node in tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    namespace = {
        'asyncio': asyncio,
        'json': json,
        'os': os,
        'Random': Random,
        'text': text,
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(ROOT / 'app' / 'seed.py'), 'exec'), namespace)
    return namespace


def test_force_reseed_one_clears_players_and_reseeds(monkeypatch):
    namespace = _load_seed()
    session = _Session(count=3000)
    monkeypatch.setenv('FORCE_RESEED', '1')
    namespace['SessionLocal'] = _SessionFactory(session)
    namespace['REAL_ORDER'] = []
    namespace['LEGEND_NAMES'] = set()
    namespace['REAL_PLAYER_STATS'] = {}
    namespace['TOTAL_PLAYERS'] = 3

    result = asyncio.run(namespace['seed']())

    assert result == 3
    assert 'DELETE FROM club_players' in session.sql
    assert 'DELETE FROM players' in session.sql
    assert "SELECT setval('players_id_seq', 1, FALSE)" in session.sql
    assert not any('SELECT count(*) FROM players' in sql for sql in session.sql)
    assert session.commits == 1


def test_force_reseed_zero_keeps_existing_players(monkeypatch):
    namespace = _load_seed()
    session = _Session(count=3000)
    monkeypatch.setenv('FORCE_RESEED', '0')
    namespace['SessionLocal'] = _SessionFactory(session)

    result = asyncio.run(namespace['seed']())

    assert result == 3000
    assert any('SELECT count(*) FROM players' in sql for sql in session.sql)
    assert not any('DELETE FROM club_players' in sql for sql in session.sql)
    assert not any('DELETE FROM players' in sql for sql in session.sql)
    assert not any("SELECT setval('players_id_seq', 1, FALSE)" in sql for sql in session.sql)
    assert session.commits == 1
