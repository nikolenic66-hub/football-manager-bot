from pathlib import Path

from app.game.season import deterministic_match_seed, new_season_seed, SeasonState, next_season_state


def test_100_seasons_deterministic_results():
    def run(seed):
        return [(deterministic_match_seed(seed, match_id), (deterministic_match_seed(seed, match_id) * 31) % 7)
                for match_id in range(1, 241)]
    for season_no in range(100):
        seed = 10_000 + season_no
        assert run(seed) == run(seed)


def test_seed_is_season_plus_match_id():
    assert deterministic_match_seed(123456, 789) == 124245


def test_reliability_schema_and_failed_retry_contracts():
    root = Path(__file__).resolve().parents[1]
    migration = (root / 'migrations' / '053_season_reliability.sql').read_text(encoding='utf-8')
    queue = (root / 'app' / 'game' / 'event_queue.py').read_text(encoding='utf-8')
    assert 'season_seed' in migration
    assert 'CONTRACT_WINDOW' in migration
    assert 'reward_claimed' in migration
    assert 'attempts' in migration and 'last_error' in migration
    assert "status IN ('PENDING','PROCESSING','DONE','FAILED','CANCELLED')" in migration
    assert "SET status='FAILED'" in queue
    assert 'retry_failed_events' in queue
    assert 'CAST(:scheduled_at AS timestamptz)' in queue


def test_state_cycle_is_five_phase_reliable_cycle():
    state = SeasonState.PRESEASON
    for expected in (SeasonState.ACTIVE, SeasonState.SEASON_END, SeasonState.OFFSEASON, SeasonState.CONTRACT_WINDOW, SeasonState.PRESEASON):
        state = next_season_state(state)
        assert state is expected


def test_p8_2_event_reliability_contracts():
    root = Path(__file__).resolve().parents[1]
    queue = (root / 'app' / 'game' / 'event_queue.py').read_text(encoding='utf-8')
    migration = (root / 'migrations' / '054_event_reliability.sql').read_text(encoding='utf-8')
    assert 'idempotency_key' in queue
    assert 'recover_stale_processing_events' in queue
    assert 'async with session.begin_nested()' in queue
    assert "season_id is required in event payload" in queue
    assert 'idempotency_key' in migration
    assert 'processing_at' in migration
    assert 'uq_season_events_idempotency_key' in migration


def test_p8_2_no_silent_global_tournament_failure():
    season = (Path(__file__).resolve().parents[1] / 'app' / 'game' / 'season.py').read_text(encoding='utf-8')
    assert 'except (ImportError, ValueError):' not in season
