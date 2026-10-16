import os
import uuid
from pathlib import Path

import pytest

asyncpg = pytest.importorskip('asyncpg')
TEST_DATABASE_URL = os.getenv('TEST_DATABASE_URL')
RUN_DB_TESTS = os.getenv('FMB_RUN_DB_TESTS') == '1'
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL or not RUN_DB_TESTS,
    reason='Real PostgreSQL sequence regression requires FMB_RUN_DB_TESTS=1 and TEST_DATABASE_URL',
)

from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import create_async_engine

ROOT = Path(__file__).resolve().parents[1]


def _sa_url(url: str) -> str:
    return url if url.startswith('postgresql+asyncpg://') else url.replace('postgresql://', 'postgresql+asyncpg://', 1)


@pytest.mark.asyncio
async def test_three_player_inserts_advance_player_sequence():
    base = make_url(_sa_url(TEST_DATABASE_URL))
    admin_url = base.set(database='postgres')
    db_name = f'fmb_seq_{uuid.uuid4().hex[:12]}'
    admin_engine = create_async_engine(admin_url.render_as_string(hide_password=False))
    try:
        async with admin_engine.connect() as conn:
            await conn.execution_options(isolation_level='AUTOCOMMIT').execute(text(f'CREATE DATABASE "{db_name}"'))
    finally:
        await admin_engine.dispose()

    db_url = base.set(database=db_name).render_as_string(hide_password=False)
    engine = create_async_engine(db_url)
    try:
        from app.db import _split_sql_statements
        async with engine.begin() as conn:
            for number in range(1, 20):
                matches = sorted(ROOT.glob(f'migrations/{number:03d}_*.sql'))
                assert len(matches) == 1
                for statement in _split_sql_statements(matches[0].read_text(encoding='utf-8')):
                    await conn.execute(text(statement))
            # Simulate the seeded/manual-ID state, then apply the force-sync migration.
            await conn.execute(text("""
                INSERT INTO players(id,first_name,last_name,nationality,age,position,pace,shooting,passing,
                    dribbling,defending,physical,stamina,mental,market_value,salary,potential)
                VALUES(1248,'Seed','Player','POL',25,'CM',70,70,70,70,70,70,70,70,100000,3000,80)
            """))
            for statement in _split_sql_statements((ROOT / 'migrations/024_force_player_sequence_sync.sql').read_text(encoding='utf-8')):
                await conn.execute(text(statement))
            ids = []
            for suffix in range(3):
                ids.append((await conn.execute(text("""
                    INSERT INTO players(first_name,last_name,nationality,age,position,pace,shooting,passing,
                        dribbling,defending,physical,stamina,mental,market_value,salary,potential)
                    VALUES(:first,:last,'POL',25,'CM',70,70,70,70,70,70,70,70,100000,3000,80)
                    RETURNING id
                """), {'first': f'Reg{suffix}', 'last': 'Test'})).scalar_one())
            max_id = (await conn.execute(text('SELECT MAX(id) FROM players'))).scalar_one()
    finally:
        await engine.dispose()
        cleanup_engine = create_async_engine(admin_url.render_as_string(hide_password=False))
        try:
            async with cleanup_engine.connect() as conn:
                await conn.execution_options(isolation_level='AUTOCOMMIT').execute(text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))
        finally:
            await cleanup_engine.dispose()

    assert len(set(ids)) == 3
    assert ids == [1249, 1250, 1251]
    assert max_id == 1251
