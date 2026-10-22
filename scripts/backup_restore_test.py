#!/usr/bin/env python3
"""Manual PostgreSQL backup/restore verification.

Usage:
    DATABASE_URL=postgresql://... python scripts/backup_restore_test.py

The script creates a temporary database, restores a custom-format pg_dump,
checks table and foreign-key counts, then drops the temporary database.
For managed providers where the application user cannot CREATE DATABASE,
set BACKUP_ADMIN_DATABASE_URL to an administrative PostgreSQL URL.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import uuid
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def require_url(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise SystemExit(f"{name} is required")
    return value.replace("postgresql+asyncpg://", "postgresql://", 1)


def run(*args: str) -> str:
    proc = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode:
        raise RuntimeError(f"command failed ({proc.returncode}): {' '.join(args)}\n{proc.stderr}")
    return proc.stdout


def database_name(url: str) -> str:
    return urlsplit(url).path.lstrip("/")


def replace_database(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/" + name, parts.query, parts.fragment))


def safe_ident(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(f"unsafe database name: {value}")
    return value


def main() -> int:
    source = require_url("DATABASE_URL")
    admin = os.getenv("BACKUP_ADMIN_DATABASE_URL") or replace_database(source, "postgres")
    source_db = database_name(source)
    admin_db = database_name(admin)
    dump_path = os.getenv("BACKUP_DUMP_PATH", f"/tmp/football-manager-{uuid.uuid4().hex}.dump")
    test_db = safe_ident(f"fm_restore_test_{uuid.uuid4().hex[:12]}")
    test_url = replace_database(admin, test_db)

    try:
        print(f"[1/5] pg_dump {source_db}")
        run("pg_dump", "-Fc", "--no-owner", "--no-acl", "--dbname", source, "-f", dump_path)
        print(f"[2/5] create temporary database {test_db}")
        run("createdb", "--dbname", admin, test_db)
        print("[3/5] restore dump")
        run("pg_restore", "--no-owner", "--no-acl", "--dbname", test_url, dump_path)
        print("[4/5] verify tables and foreign keys")
        table_sql = "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='r';"
        fk_sql = "SELECT count(*) FROM pg_constraint WHERE contype='f';"
        source_tables = int(run("psql", "--tuples-only", "--no-align", "--dbname", source, table_sql).strip())
        source_fks = int(run("psql", "--tuples-only", "--no-align", "--dbname", source, fk_sql).strip())
        table_count = int(run("psql", "--tuples-only", "--no-align", "--dbname", test_url, table_sql).strip())
        fk_count = int(run("psql", "--tuples-only", "--no-align", "--dbname", test_url, fk_sql).strip())
        if table_count != source_tables:
            raise RuntimeError(f"table count mismatch: source={source_tables}, restored={table_count}")
        if fk_count != source_fks:
            raise RuntimeError(f"foreign-key count mismatch: source={source_fks}, restored={fk_count}")
        if table_count == 0:
            raise RuntimeError("restore contains zero public tables")
        # PostgreSQL cannot create a valid FK constraint against a missing
        # relation, so a successful restore plus equal FK count verifies the
        # referenced table graph is present.
        print(f"PASS: restored tables={table_count}, foreign_keys={fk_count}")
        print("[5/5] temporary database will be removed")
        return 0
    finally:
        subprocess.run(["dropdb", "--if-exists", "--dbname", admin, test_db], check=False)
        try:
            os.unlink(dump_path)
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
