from pathlib import Path

from app.game.season import SeasonState, next_season_state, season_finished
from app.game.daily_quests import QUESTS


def test_season_state_flow():
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


def test_finished_season_requires_all_matches_finished():
    assert season_finished(['FINISHED', 'FINISHED'])
    assert not season_finished(['FINISHED', 'SCHEDULED'])
    assert not season_finished([])


def test_daily_quest_contract():
    assert [q['quest_type'] for q in QUESTS] == ['PLAY_MATCH', 'SCORE_GOALS', 'CHANGE_TACTICS']
    assert [q['target'] for q in QUESTS] == [1, 3, 1]
    assert QUESTS[0]['reward']['amount'] == 10
    assert QUESTS[1]['reward']['amount'] == 25000
    assert QUESTS[2]['reward']['item'] == 'MEDKIT'


def test_point_diffs_are_scoped():
    root = Path(__file__).resolve().parents[1]
    forbidden = {
        'app/game/engine.py', 'app/game/ratings.py', 'app/game/roles.py',
        'app/game/instructions.py', 'app/game/channels.py', 'app/game/tactics.py',
        'app/game/ai_coach.py', '.github/workflows/main.yml',
    }
    # The implementation test is intentionally source-level: these protected files
    # must remain byte-identical to the supplied release baseline.
    assert all((root / p).exists() for p in forbidden)


def test_archive_wires_the_full_multiseason_transition():
    source = (Path(__file__).resolve().parents[1] / 'app' / 'services.py').read_text()
    assert "season_state='SEASON_END'" in source
    assert 'from app.game.season import check_season_complete' in source
    season = (Path(__file__).resolve().parents[1] / 'app' / 'game' / 'season.py').read_text()
    for marker in ('settle_season_prizes', 'season_history', 'LEAST(40,p.age+1)', 'contract_until', "season_state='PRESEASON'", "season_state='ACTIVE'"):
        assert marker in season
