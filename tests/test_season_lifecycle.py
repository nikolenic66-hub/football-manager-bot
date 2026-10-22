from app.game.season import SeasonState, next_season_state, season_finished, _promotion_pairs


def test_season_state_cycle_includes_contract_window():
    state = SeasonState.PRESEASON
    state = next_season_state(state)
    assert state is SeasonState.ACTIVE
    state = next_season_state(state)
    assert state is SeasonState.SEASON_END
    state = next_season_state(state)
    assert state is SeasonState.OFFSEASON
    state = next_season_state(state)
    assert state is SeasonState.CONTRACT_WINDOW
    assert next_season_state(state) is SeasonState.PRESEASON


def test_season_complete_requires_finished_matches():
    assert season_finished(['FINISHED', 'FINISHED'])
    assert not season_finished(['FINISHED', 'SCHEDULED'])
    assert not season_finished([])


def test_promotion_relegation_pair_is_two_up_two_down():
    leagues = [{'id': 1, 'tier': 'BRONZE'}, {'id': 2, 'tier': 'SILVER'}]
    pairs = _promotion_pairs(leagues)
    assert [(a['id'], b['id']) for a, b in pairs] == [(1, 2)]


def test_season_code_has_idempotent_player_checkpoint_and_prizes():
    source = open('app/game/season.py', encoding='utf-8').read()
    assert 'ON CONFLICT(player_id,season_id) DO NOTHING' in source
    assert "state='SEASON_END'" in source
    assert 'settle_season_prizes' in source
    assert 'reward_claimed' in source
    assert 'CONTRACT_WINDOW' in source
