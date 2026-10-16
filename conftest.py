import os

if not (os.getenv("TEST_DATABASE_URL") and os.getenv("FMB_RUN_DB_TESTS") == "1"):
    collect_ignore = ["tests/test_migration_020_db.py"]
