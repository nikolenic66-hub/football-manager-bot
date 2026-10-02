from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from .config import settings

engine = create_async_engine(
    settings.database_url.replace(
        "postgresql://",
        "postgresql+asyncpg://",
        1,
    ),
    pool_pre_ping=True,
)

SessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


async def init_db():
    root = Path(__file__).resolve().parent.parent

    async with engine.begin() as conn:
        for migration in sorted((root / "migrations").glob("*.sql")):
            sql = migration.read_text(encoding="utf-8")

            for statement in sql.split(";"):
                statement = statement.strip()

                if statement:
                    await conn.exec_driver_sql(statement)
