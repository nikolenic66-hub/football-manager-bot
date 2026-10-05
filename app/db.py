from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import text

from .config import settings

engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_recycle=1800,
    pool_size=2,
    max_overflow=0,
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db():
    """Apply versioned SQL migrations once.

    Existing installations created by the old bootstrap runner are detected
    through the presence of the users table and marked as migrated without
    re-running historical/destructive SQL. Fresh databases run all migrations
    exactly once.
    """
    root = Path(__file__).resolve().parent.parent
    migrations = sorted((root / 'migrations').glob('*.sql'))
    async with engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                filename TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        existing = (await conn.execute(text("SELECT to_regclass('public.users')"))).scalar_one()
        applied = set((await conn.execute(text('SELECT version FROM schema_migrations'))).scalars().all())
        if existing and not applied:
            # The previous release executed migrations 001..011 on every boot.
            # Preserve that database as-is and only replay migrations introduced
            # after the old bootstrap runner.
            for migration in migrations:
                version = int(migration.stem.split('_', 1)[0])
                if version <= 11:
                    await conn.execute(
                        text('INSERT INTO schema_migrations(version,filename) VALUES(:v,:f)'),
                        {'v': version, 'f': migration.name},
                    )
            applied = {int(m.stem.split('_', 1)[0]) for m in migrations if int(m.stem.split('_', 1)[0]) <= 11}
        for migration in migrations:
            version = int(migration.stem.split('_', 1)[0])
            if version in applied:
                continue
            await conn.exec_driver_sql(migration.read_text(encoding='utf-8'))
            await conn.execute(
                text('INSERT INTO schema_migrations(version,filename) VALUES(:v,:f)'),
                {'v': version, 'f': migration.name},
            )
