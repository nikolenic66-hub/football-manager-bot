from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import settings


engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_recycle=1800,
    pool_size=2,
    max_overflow=0,
)

SessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


def _split_sql_statements(sql: str) -> list[str]:
    """Split PostgreSQL SQL without breaking quoted strings or dollar blocks.

    Migrations may contain ordinary semicolon-separated statements as well as
    PL/pgSQL blocks such as DO $$ ... END $$;. SQLAlchemy's asyncpg prepared
    execution path accepts one statement at a time, so keep the splitter here
    instead of relying on driver-specific multi-statement behavior.
    """
    statements: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(sql)

    quote: str | None = None
    dollar_tag: str | None = None
    line_comment = False
    block_comment = False

    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""

        if line_comment:
            buf.append(ch)
            if ch == "\n":
                line_comment = False
            i += 1
            continue

        if block_comment:
            buf.append(ch)
            if ch == "*" and nxt == "/":
                buf.append(nxt)
                i += 2
                block_comment = False
                continue
            i += 1
            continue

        if dollar_tag is not None:
            if sql.startswith(dollar_tag, i):
                buf.append(dollar_tag)
                i += len(dollar_tag)
                dollar_tag = None
            else:
                buf.append(ch)
                i += 1
            continue

        if quote is not None:
            buf.append(ch)

            if ch == quote:
                if nxt == quote:
                    buf.append(nxt)
                    i += 2
                    continue
                quote = None

            elif ch == "\\" and quote == "'" and i + 1 < n:
                buf.append(nxt)
                i += 2
                continue

            i += 1
            continue

        if ch == "-" and nxt == "-":
            buf.extend((ch, nxt))
            i += 2
            line_comment = True
            continue

        if ch == "/" and nxt == "*":
            buf.extend((ch, nxt))
            i += 2
            block_comment = True
            continue

        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            i += 1
            continue

        if ch == "$":
            j = i + 1

            while j < n and (sql[j].isalnum() or sql[j] == "_"):
                j += 1

            if j < n and sql[j] == "$":
                dollar_tag = sql[i:j + 1]
                buf.append(dollar_tag)
                i = j + 1
                continue

        if ch == ";":
            statement = "".join(buf).strip()

            if statement:
                statements.append(statement)

            buf.clear()
            i += 1
            continue

        buf.append(ch)
        i += 1

    statement = "".join(buf).strip()

    if statement:
        statements.append(statement)

    return statements


async def init_db():
    """Apply versioned SQL migrations once.

    Existing installations created by the old bootstrap runner are detected
    through the presence of the users table and marked as migrated without
    re-running historical/destructive SQL. Fresh databases run all migrations
    exactly once.
    """
    root = Path(__file__).resolve().parent.parent
    migrations = sorted((root / "migrations").glob("*.sql"))

    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    filename TEXT NOT NULL,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        )

        existing = (
            await conn.execute(
                text
