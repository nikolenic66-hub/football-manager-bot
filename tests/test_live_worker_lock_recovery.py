from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_live_worker_checks_and_reacquires_lost_session_lock():
    source = (ROOT / "app" / "live_worker.py").read_text(encoding="utf-8")
    assert "async def _open_live_worker_lock()" in source
    assert 'await lock_conn.execute(text("SELECT 1"))' in source
    assert "lock_conn = await _open_live_worker_lock()" in source
    assert "Live-worker lock connection was lost; reconnecting" in source


def test_live_worker_does_not_unlock_a_known_closed_connection():
    source = (ROOT / "app" / "live_worker.py").read_text(encoding="utf-8")
    final_block = source[source.index("    finally:", source.index("async def live_worker")):]
    assert "if not lock_conn.closed and not lock_conn.invalidated" in final_block
    assert "PostgreSQL releases it when the session closes." in final_block
