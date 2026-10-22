import asyncio
import os
import uuid
from pathlib import Path

import pytest

asyncpg = pytest.importorskip('asyncpg')

TEST_DATABASE_URL = os.getenv('TEST_DATABASE_URL')
RUN_DB_TESTS = os.getenv('FMB_RUN_DB_TESTS') == '1'

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL or not RUN_DB_TESTS,
    reason='Real PostgreSQL regression tests require FMB_RUN_DB_TESTS=1 and TEST_DATABASE_URL',
)

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import text, make_url

from app.services import add_copy_to_club, build_starting_lineup, buy_player, full_reset



ROOT = Path(__file__).resolve().parents[1]

async def _apply_migration(conn, path: Path):
    from app.db import _split_sql_statements
    for statement in _split_sql_statements(path.read_text(encoding='utf-8')):
        await conn.execute(text(statement))

def _sa_url(url: str) -> str:
    if url.startswith('postgresql+asyncpg://'):
        return url
    return url.replace('postgresql://', 'postgresql+asyncpg://', 1)


@pytest.fixture(scope='module')
async def db():
    engine = create_async_engine(_sa_url(TEST_DATABASE_URL), pool_size=4, max_overflow=0)
    async with engine.connect() as conn:
        required = await conn.execute(text("""
            SELECT COUNT(*) FROM information_schema.tables
            WHERE table_schema='public' AND table_name IN ('players','clubs','users','club_players','club_lineups')
        """))
        if int(required.scalar_one()) != 5:
            pytest.fail('TEST_DATABASE_URL must point to a migrated football-manager database')
    yield engine
    await engine.dispose()


@pytest.fixture
async def sandbox(db):
    tag = f'pytest-template-{os.getpid()}-{uuid.uuid4().hex[:10]}'
    async with db.begin() as conn:
        user_id = int((await conn.execute(text("""
            INSERT INTO users(telegram_id,username,first_name)
            VALUES(:tg,:username,:first_name) RETURNING id
        """), {'tg': -abs(hash(tag)) % 2_000_000_000, 'username': tag, 'first_name': 'DB Test'})).scalar_one())
        club_id = int((await conn.execute(text("""
            INSERT INTO clubs(owner_user_id,name,short_name,stadium_name,budget,fans,reputation,ticket_price)
            VALUES(:u,:name,:short,:stadium,2000000,5000,1,20) RETURNING id
        """), {'u': user_id, 'name': tag, 'short': 'TST', 'stadium': f'{tag} Arena'})).scalar_one())
        # One complete 23-card template pool matching the production rarity/position slots.
        specs = [
            ('GK','BASE'),('LB','BASE'),('RB','BASE'),('GK','BASE'),('GK','BASE'),
            ('CB','BASE'),('CB','BASE'),('LB','BASE'),('RB','BASE'),('DM','BASE'),
            ('AM','BASE'),('LW','BASE'),('ST','BASE'),
            ('CB','RARE'),('CB','RARE'),('CM','RARE'),('CM','RARE'),('LW','RARE'),('RW','RARE'),
            ('CM','EPIC'),('ST','EPIC'),
            ('GK','BASE'),('CM','BASE'),
        ]
        # Add a few extra templates so fallback/order logic is not dependent on exact IDs.
        for i, (pos, rarity) in enumerate(specs):
            await conn.execute(text("""
                INSERT INTO players(
                    first_name,last_name,nationality,age,position,pace,shooting,passing,dribbling,
                    defending,physical,stamina,mental,market_value,salary,potential,rarity,card_version,
                    real_player,preferred_foot,secondary_positions,portrait_url,portrait_source,talents,
                    tactical_archetype,data_source,is_seed,is_template,template_id)
                VALUES(:f,:l,'TST',25,:pos,70,70,70,70,70,70,70,70,100000,3000,80,:rarity,'STANDARD',
                       FALSE,'R','[]',NULL,NULL,'[]','BALANCED','pytest',FALSE,TRUE,NULL)
            """), {'f': f'Test{i}', 'l': tag, 'pos': pos, 'rarity': rarity})
    yield db, club_id, user_id, tag
    async with db.begin() as conn:
        await conn.execute(text('DELETE FROM club_players WHERE club_id=:c'), {'c': club_id})
        await conn.execute(text('DELETE FROM clubs WHERE id=:c'), {'c': club_id})
        await conn.execute(text('DELETE FROM users WHERE id=:u'), {'u': user_id})
        await conn.execute(text("DELETE FROM players WHERE data_source LIKE 'pytest%' AND last_name=:tag"), {'tag': tag})


@pytest.mark.asyncio
async def test_db_full_reset_rebuilds_real_club_squad(sandbox):
    db, club_id, user_id, tag = sandbox
    Session = async_sessionmaker(db, expire_on_commit=False, class_=AsyncSession)
    async with Session() as s:
        await s.execute(text("UPDATE clubs SET budget=12345,fans=1,reputation=2,stadium_level=3,ticket_price=99,sponsor_level=4,debt=777 WHERE id=:c"), {'c': club_id})
        await s.commit()
        await full_reset(s, user_id)
        await s.commit()
    async with db.connect() as conn:
        count = int((await conn.execute(text('SELECT COUNT(*) FROM club_players WHERE club_id=:c'), {'c': club_id})).scalar_one())
        lineup = (await conn.execute(text('SELECT player_ids FROM club_lineups WHERE club_id=:c'), {'c': club_id})).scalar_one()
        owned = set((await conn.execute(text('SELECT player_id FROM club_players WHERE club_id=:c'), {'c': club_id})).scalars().all())
        club = (await conn.execute(text('SELECT budget,fans,reputation,stadium_level,ticket_price,sponsor_level,debt FROM clubs WHERE id=:c'), {'c': club_id})).one()
        copies = int((await conn.execute(text('SELECT COUNT(*) FROM players WHERE data_source=\'pytest\' AND last_name=:tag AND is_template=FALSE'), {'tag': tag})).scalar_one())
        duplicates = (await conn.execute(text('''
            SELECT cp.club_id,p.template_id,COUNT(*)
            FROM club_players cp JOIN players p ON p.id=cp.player_id
            WHERE cp.club_id=:c
            GROUP BY cp.club_id,p.template_id
            HAVING COUNT(*) > 1
        '''), {'c': club_id})).all()
        null_templates = int((await conn.execute(text(
            'SELECT COUNT(*) FROM club_players WHERE club_id=:c AND template_id IS NULL'
        ), {'c': club_id})).scalar_one())
        index_def = (await conn.execute(text(
            "SELECT indexdef FROM pg_indexes WHERE schemaname='public' AND indexname='one_copy_per_template_per_club'"
        ))).scalar_one_or_none()
    assert count == 23
    assert len(lineup) == 11
    assert set(lineup).issubset(owned)
    assert club == (2_000_000, 5_000, 1, 1, 20, 1, 0)
    assert duplicates == []
    assert null_templates == 0
    assert copies == 23
    assert index_def is not None
    assert 'where' not in index_def.lower()
    assert '(club_id, template_id)' in index_def


@pytest.mark.asyncio
async def test_db_build_starting_lineup_stores_copy_ids(sandbox):
    db, club_id, _, _ = sandbox
    Session = async_sessionmaker(db, expire_on_commit=False, class_=AsyncSession)
    async with Session() as s:
        await build_starting_lineup(s, club_id)
        await s.commit()
    async with db.connect() as conn:
        lineup = (await conn.execute(text('SELECT player_ids FROM club_lineups WHERE club_id=:c'), {'c': club_id})).scalar_one()
        owned = set((await conn.execute(text('SELECT player_id FROM club_players WHERE club_id=:c'), {'c': club_id})).scalars().all())
        rows = (await conn.execute(text("""
            SELECT p.rarity, COUNT(*) FROM club_players cp JOIN players p ON p.id=cp.player_id
            WHERE cp.club_id=:c GROUP BY p.rarity
        """), {'c': club_id})).all()
    assert set(lineup).issubset(owned)
    assert len(owned) == 23
    assert dict(rows) == {'BASE': 15, 'RARE': 6, 'EPIC': 2}


@pytest.mark.asyncio
async def test_db_buy_player_rejects_duplicate_template(sandbox):
    db, club_id, _, tag = sandbox
    Session = async_sessionmaker(db, expire_on_commit=False, class_=AsyncSession)
    async with db.begin() as conn:
        template_id = int((await conn.execute(text("SELECT id FROM players WHERE data_source='pytest' AND last_name=:tag AND rarity='EPIC' LIMIT 1"), {'tag': tag})).scalar_one())
        copy_id = int((await conn.execute(text("""
            INSERT INTO players(first_name,last_name,nationality,age,position,pace,shooting,passing,dribbling,defending,physical,stamina,mental,market_value,salary,potential,rarity,card_version,real_player,preferred_foot,secondary_positions,portrait_url,portrait_source,talents,tactical_archetype,data_source,is_seed,is_template,template_id)
            SELECT first_name,last_name,nationality,age,position,pace,shooting,passing,dribbling,defending,physical,stamina,mental,market_value,salary,potential,rarity,card_version,real_player,preferred_foot,secondary_positions,NULL,NULL,talents,tactical_archetype,'pytest-copy',FALSE,FALSE,:t
            FROM players WHERE id=:t RETURNING id
        """), {'t': template_id})).scalar_one())
        await conn.execute(text('INSERT INTO club_players(club_id,player_id,template_id) VALUES(:c,:p,:t)'), {'c': club_id, 'p': copy_id, 't': template_id})
        market_copy = int((await conn.execute(text("""
            INSERT INTO players(first_name,last_name,nationality,age,position,pace,shooting,passing,dribbling,defending,physical,stamina,mental,market_value,salary,potential,rarity,card_version,real_player,preferred_foot,secondary_positions,portrait_url,portrait_source,talents,tactical_archetype,data_source,is_seed,is_template,template_id)
            SELECT first_name,last_name,nationality,age,position,pace,shooting,passing,dribbling,defending,physical,stamina,mental,market_value,salary,potential,rarity,card_version,real_player,preferred_foot,secondary_positions,NULL,NULL,talents,tactical_archetype,'pytest-market',FALSE,FALSE,:t
            FROM players WHERE id=:t RETURNING id
        """), {'t': template_id})).scalar_one())
    async with Session() as s:
        with pytest.raises(ValueError, match='У вас уже есть такой игрок'):
            await buy_player(s, club_id, market_copy)
        await s.rollback()


@pytest.mark.asyncio
async def test_db_concurrent_copy_creation_has_one_owner_and_no_compensation(sandbox):
    db, club_id, _, tag = sandbox
    async with db.connect() as conn:
        template_id = int((await conn.execute(text("SELECT id FROM players WHERE data_source='pytest' AND last_name=:tag AND rarity='EPIC' LIMIT 1"), {'tag': tag})).scalar_one())
    Session = async_sessionmaker(db, expire_on_commit=False, class_=AsyncSession)

    async def worker():
        async with Session() as s:
            result = await add_copy_to_club(s, club_id, template_id)
            await s.commit()
            return result

    results = await asyncio.gather(worker(), worker())
    assert sorted(r[1] for r in results) == [0, 0]
    async with db.connect() as conn:
        count = int((await conn.execute(text("""
            SELECT COUNT(*) FROM club_players WHERE club_id=:c AND template_id=:t
        """), {'c': club_id, 't': template_id})).scalar_one())
    assert count == 1


@pytest.fixture
async def legacy_migration_db():
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
async def test_db_migration_020_real_postgres_promotes_seed_but_not_legacy_generated_admin(legacy_migration_db):
    async with legacy_migration_db.connect() as conn:
        admin_row = (await conn.execute(text(
            'SELECT is_seed,is_template,template_id FROM players WHERE id=500'
        ))).one()
        seed_row = (await conn.execute(text(
            'SELECT is_seed,is_template,template_id FROM players WHERE id=100'
        ))).one()
    assert admin_row == (False, False, None)
    assert seed_row == (True, True, None)


@pytest.mark.asyncio
async def test_db_migration_023_repairs_legacy_admin_marker_and_is_idempotent(legacy_migration_db):
    async with legacy_migration_db.begin() as conn:
        await conn.execute(text("""
            INSERT INTO admin_actions_log(admin_user_id,action,target_type,target_id,payload)
            VALUES(1,'player_create','player',500,'{}'::jsonb)
        """))
        await conn.execute(text("UPDATE players SET is_seed=TRUE,is_template=TRUE WHERE id=500"))
        await conn.execute(text("""
            INSERT INTO players(
                id,first_name,last_name,nationality,age,position,pace,shooting,passing,
                dribbling,defending,physical,stamina,mental,market_value,salary,potential,
                data_source,real_player,is_seed,is_template,template_id)
            VALUES(501,'Copy','Seed','POL',25,'CM',70,70,70,70,70,70,70,70,100000,3000,80,'seed',FALSE,FALSE,FALSE,100)
        """))
        await conn.execute(text("""
            INSERT INTO players(
                id,first_name,last_name,nationality,age,position,pace,shooting,passing,
                dribbling,defending,physical,stamina,mental,market_value,salary,potential,
                data_source,real_player,is_seed,is_template,template_id)
            VALUES(502,'Copy','Admin','POL',25,'CM',70,70,70,70,70,70,70,70,100000,3000,80,'admin',FALSE,FALSE,FALSE,500)
        """))
        await _apply_migration(conn, ROOT / 'migrations/024_seed_marker_repair.sql')
        await _apply_migration(conn, ROOT / 'migrations/024_seed_marker_repair.sql')
        rows = (await conn.execute(text("""
            SELECT id,is_seed,is_template,template_id
            FROM players WHERE id IN (100,500,501,502) ORDER BY id
        """))).all()
    assert rows == [(100, True, True, None), (500, False, True, None), (501, False, False, 100), (502, False, False, 500)]


@pytest.mark.asyncio
async def test_db_migration_027_allows_regular_on_conflict_target(sandbox):
    db, club_id, user_id, _ = sandbox
    Session = async_sessionmaker(db, expire_on_commit=False, class_=AsyncSession)
    async with Session() as s:
        await full_reset(s, user_id)
        await s.commit()
    async with db.begin() as conn:
        await _apply_migration(conn, ROOT / 'migrations/027_regular_unique_index.sql')
        row = (await conn.execute(text('''
            SELECT player_id, template_id
            FROM club_players
            WHERE club_id=:c
            ORDER BY id
            LIMIT 1
        '''), {'c': club_id})).one()
        inserted = (await conn.execute(text('''
            INSERT INTO club_players(club_id,player_id,template_id)
            VALUES(:c,:p,:t)
            ON CONFLICT (club_id,template_id) DO NOTHING
            RETURNING player_id
        '''), {'c': club_id, 'p': int(row.player_id), 't': int(row.template_id)})).scalar_one_or_none()
    assert inserted is None
