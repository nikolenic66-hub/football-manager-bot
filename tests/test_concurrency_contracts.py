from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_live_worker_uses_postgres_advisory_lock():
    source = (ROOT / 'app' / 'live_worker.py').read_text()
    assert 'pg_try_advisory_lock' in source
    assert 'pg_advisory_unlock' in source


def test_league_start_and_join_are_row_locked():
    source = (ROOT / 'app' / 'services.py').read_text()
    assert "FROM leagues WHERE invite_code=:code FOR UPDATE" in source
    assert "status='WAITING' ORDER BY id DESC LIMIT 1 FOR UPDATE" in source


def test_transfer_locks_clubs_in_deterministic_order():
    source = (ROOT / 'app' / 'services.py').read_text()
    assert 'ORDER BY id FOR UPDATE' in source
    assert "if buyer == seller" in source


def test_financial_match_entries_are_idempotent():
    migration = (ROOT / 'migrations' / '012_concurrency_and_indexes.sql').read_text()
    assert 'uq_match_finance_entry' in migration
    assert 'match_id,club_id,category' in migration


def test_transfer_cancel_and_accept_are_state_guarded():
    source=(ROOT/'app'/'services.py').read_text()
    assert "status='CANCELLED'" in source
    assert "status='PENDING' FOR UPDATE" in source
    assert "listing_status']!='LISTED'" in source


def test_live_restart_uses_persisted_phase_timestamp():
    source=(ROOT/'app'/'services.py').read_text()
    block=source[source.index('async def advance_live_matches'):]
    assert "match.get('phase_started_at') or match.get('started_at') or now" in block
    assert 'elapsed=(now-phase_start).total_seconds()' in block


def test_notification_claim_locks_only_notification_rows_before_join():
    source = (ROOT / 'app' / 'services.py').read_text()
    block = source[source.index('async def deliver_due_notifications'):source.index('async def ', source.index('async def deliver_due_notifications') + 10) if 'async def ' in source[source.index('async def deliver_due_notifications') + 10:] else len(source)]
    assert 'SELECT n.id' in block
    assert 'FROM notifications n' in block
    assert 'LIMIT :n\n        FOR UPDATE SKIP LOCKED' in block
    assert 'FOR UPDATE OF n' not in block


def test_club_creation_does_not_mask_unrelated_integrity_errors_as_name_taken():
    source = (ROOT / 'app' / 'repositories.py').read_text()
    block = source[source.index('async def create_club'): ]
    assert 'constraint_name' in block
    assert "clubs_name_key" in block
    assert "uq_clubs_owner_user" in block
    assert 'await s.rollback()' not in block
    assert "WHERE lower(name)=lower(:n)" not in block
    assert 'raise\n' in block


def test_rookie_league_autojoin_and_hourly_bot_fill_contract():
    source=(ROOT/'app'/'services.py').read_text()
    worker=(ROOT/'app'/'live_worker.py').read_text()
    assert "tier='ROOKIE'" in source
    assert "pg_advisory_xact_lock(hashtext('rookie-league-auto-join'))" in source
    assert 'async def auto_join_rookie_league' in source
    assert 'async def fill_waiting_rookie_leagues' in source
    assert "INTERVAL '24 hours'" in source
    assert 'create_test_bot_club(s)' in source
    assert 'Лига [ROOKIE] набрана (добита ботами), старт сезона' in source
    assert 'rookie_check_at=now_mono+3600.0' in worker


def test_tournament_calendar_and_match_competition_type_contract():
    source=(ROOT/'app'/'services.py').read_text()
    migration=(ROOT/'migrations'/'039_tournaments.sql').read_text()
    assert "competition_type,started_at,scheduled_at" in source
    assert "'LEAGUE',NULL,CAST(:kickoff AS timestamptz)" in source
    assert 'match_interval_days' in source
    assert 'match_time' in source
    assert 'start_date' in source
    assert "candidate.weekday()==6" in source
    assert "ADD COLUMN IF NOT EXISTS tier VARCHAR(20) NOT NULL DEFAULT 'ROOKIE'" in migration
    assert "CHECK (tier IN ('ROOKIE','BRONZE','SILVER','GOLD','DIAMOND','ELITE','LEGEND'))" in migration
    assert "CHECK (competition_type IN ('LEAGUE','CUP','CHAMPIONS'))" in migration
