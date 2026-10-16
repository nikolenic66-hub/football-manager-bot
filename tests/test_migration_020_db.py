import os
import uuid
from pathlib import Path

import pytest

asyncpg = pytest.importorskip('asyncpg')

TEST_DATABASE_URL = os.getenv('TEST_DATABASE_URL')
RUN_DB_TESTS = os.getenv('FMB_RUN_DB_TESTS') == '1'

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL or not RUN_DB_TESTS,
    reason='Real PostgreSQL migration tests require FMB_RUN_DB_TESTS=1 and TEST_DATABASE_URL',
)

from sqlalchemy import text, make_url
from sqlalchemy.ext.asyncio import create_async_engine

ROOT = Path(__file__).resolve().parents[1]


def _sa_url(url: str) -> str:
    if url.startswith('postgresql+asyncpg://'):
        return url
    return url.replace('postgresql://', 'postgresql+asyncpg://', 1)


async def _apply_migration(conn, path: Path):
    # Import only when the real DB test is enabled; app.db requires DATABASE_URL.
    from app.db import _split_sql_statements

    for statement in _split_sql_statements(path.read_text(encoding='utf-8')):
        await conn.execute(text(statement))


@pytest.fixture
async def migration_db():
    base = make_url(_sa_url(TEST_DATABASE_URL))
    admin_url = base.set(database='postgres')
    db_name = f'fmb_m020_{uuid.uuid4().hex[:12]}'
    admin_engine = create_async_engine(admin_url.render_as_string(hide_password=False))
    try:
        async with admin_engine.connect() as conn:
            await conn.execution_options(isolation_level='AUTOCOMMIT').execute(
                text(f'CREATE DATABASE "{db_name}"')
            )
    finally:
        await admin_engine.dispose()

    db_url = base.set(database=db_name).render_as_string(hide_password=False)
    engine = create_async_engine(db_url)
    try:
        async with engine.begin() as conn:
            for number in range(1, 20):
                matches = sorted(ROOT.glob(f'migrations/{number:03d}_*.sql'))
                assert len(matches) == 1, f'missing migration {number:03d}'
                await _apply_migration(conn, matches[0])

            # Legacy admin/user row: before the marker split this could have been
            # data_source='admin'. It must not be promoted by 020.
            await conn.execute(text("""
                INSERT INTO players(
                    id,first_name,last_name,nationality,age,position,pace,shooting,passing,
                    dribbling,defending,physical,stamina,mental,market_value,salary,potential,
                    data_source,real_player)
                VALUES(500,'Legacy','Admin','POL',25,'CM',70,70,70,70,70,70,70,70,100000,3000,80,'generated',FALSE)
            """))
            await conn.execute(text("""
                INSERT INTO players(
                    id,first_name,last_name,nationality,age,position,pace,shooting,passing,
                    dribbling,defending,physical,stamina,mental,market_value,salary,potential,
                    data_source,real_player)
                VALUES(100,'Seed','Synthetic','POL',25,'CM',70,70,70,70,70,70,70,70,100000,3000,80,'seed',FALSE)
            """))

            await _apply_migration(conn, ROOT / 'migrations/020_player_templates.sql')

        yield engine
    finally:
        await engine.dispose()
        cleanup_engine = create_async_engine(admin_url.render_as_string(hide_password=False))
        try:
            async with cleanup_engine.connect() as conn:
                await conn.execution_options(isolation_level='AUTOCOMMIT').execute(
                    text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
                )
        finally:
            await cleanup_engine.dispose()


@pytest.mark.asyncio
async def test_migration_020_real_postgres_promotes_seed_but_not_legacy_generated_admin(migration_db):
    async with migration_db.connect() as conn:
        admin_row = (await conn.execute(text(
            'SELECT is_seed,is_template,template_id FROM players WHERE id=500'
        ))).one()
        seed_row = (await conn.execute(text(
            'SELECT is_seed,is_template,template_id FROM players WHERE id=100'
        ))).one()

    assert admin_row == (False, False, None)
    assert seed_row == (True, True, None)


@pytest.mark.asyncio
async def test_migration_023_repairs_legacy_admin_marker_and_is_idempotent(migration_db):
    async with migration_db.begin() as conn:
        await conn.execute(text("""
            INSERT INTO admin_actions_log(admin_user_id,action,target_type,target_id,payload)
            VALUES(1,'player_create','player',500,'{}'::jsonb)
        """))
        await conn.execute(text(
            "UPDATE players SET is_seed=TRUE,is_template=TRUE WHERE id=500"
        ))
        await _apply_migration(conn, ROOT / 'migrations/023_seed_marker_repair.sql')
        await _apply_migration(conn, ROOT / 'migrations/023_seed_marker_repair.sql')

        rows = (await conn.execute(text("""
            SELECT id,is_seed,is_template,template_id
            FROM players WHERE id IN (100,500) ORDER BY id
        """))).all()

    assert rows == [(100, True, True, None), (500, False, True, None)]
