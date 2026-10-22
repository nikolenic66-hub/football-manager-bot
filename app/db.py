from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import settings


engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_recycle=300,
    pool_size=3,
    max_overflow=5,
    connect_args={"statement_cache_size": 0},
)

SessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


def _split_sql_statements(sql: str) -> list[str]:
    """Split PostgreSQL SQL without breaking quoted strings or dollar blocks."""

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
                dollar_tag = sql[i : j + 1]
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
    """Apply versioned SQL migrations once."""

    root = Path(__file__).resolve().parent.parent
    migrations = sorted((root / "migrations").glob("*.sql"))

    async with engine.begin() as conn:
        # The bot and FastAPI run as separate Supervisor processes and may boot
        # concurrently. Serialize schema migration checks across both processes.
        await conn.execute(text("SELECT pg_advisory_xact_lock(hashtext('football_manager_schema_migrations'))"))
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
            await conn.execute(text("SELECT to_regclass('public.users')"))
        ).scalar_one()

        applied = set(
            (
                await conn.execute(
                    text("SELECT version FROM schema_migrations")
                )
            ).scalars().all()
        )

        if existing and not applied:
            # The previous release executed migrations 001..011
            # on every boot. Preserve that database as-is and mark
            # those migrations as already applied.
            for migration in migrations:
                version = int(migration.stem.split("_", 1)[0])
                if version <= 11:
                    await conn.execute(
                        text(
                            """
                            INSERT INTO schema_migrations(version, filename)
                            VALUES (:v, :f)
                            """
                        ),
                        {"v": version, "f": migration.name},
                    )

            applied = {
                int(m.stem.split("_", 1)[0])
                for m in migrations
                if int(m.stem.split("_", 1)[0]) <= 11
            }

        for migration in migrations:
            version = int(migration.stem.split("_", 1)[0])

            if version in applied:
                continue

            sql = migration.read_text(encoding="utf-8")

            for statement in _split_sql_statements(sql):
                # Migration statements are static PostgreSQL SQL, not SQLAlchemy
                # templates. Execute them through the driver so JSON literals
                # containing fragments like ":10" are not parsed as bind params.
                await conn.exec_driver_sql(statement)

            await conn.execute(
                text(
                    """
                    INSERT INTO schema_migrations(version, filename)
                    VALUES (:v, :f)
                    """
                ),
                {"v": version, "f": migration.name},
            )


        # Defensive schema repair for databases where an earlier deployment
        # recorded migration 067 but did not actually create the column (for
        # example, after a manual restore or a partial deploy). This is
        # deliberately idempotent and runs on every startup before API routes
        # can issue SELECT/INSERT statements that reference bench_ids.
        await conn.exec_driver_sql(
            """CREATE TABLE IF NOT EXISTS club_lineups (
                club_id BIGINT PRIMARY KEY REFERENCES clubs(id) ON DELETE CASCADE,
                player_ids BIGINT[] NOT NULL DEFAULT '{}'::BIGINT[]
            )"""
        )
        await conn.exec_driver_sql(
            """ALTER TABLE club_lineups
               ADD COLUMN IF NOT EXISTS bench_ids BIGINT[] NOT NULL DEFAULT '{}'::BIGINT[]"""
        )

        # Fresh installations (and databases reset outside the admin flow) need
        # a season row before tests, leagues, or Battle Pass can operate.
        from app.game.season import ensure_initial_season
        await ensure_initial_season(conn)
