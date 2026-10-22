from app.game.board import TacticalBoard, FORMATION_SLOTS
from app.game.tactics import ALLOWED_FORMATIONS, FORMATION_PROFILE
from app.game.roles import role_effective_stats
from app.game.instructions import apply_instruction
from app.game.channels import channel_matchup, count_channel_players
from app.game.engine import formation_matchup, defensive_density


def test_ten_formations_and_profiles():
    assert len(ALLOWED_FORMATIONS) == 10
    assert set(ALLOWED_FORMATIONS) == set(FORMATION_PROFILE)
    for formation in ALLOWED_FORMATIONS:
        p = FORMATION_PROFILE[formation]
        assert all(key in p for key in ('def','mid','att','wide','dm','am'))
        assert len(FORMATION_SLOTS[formation]) == 11


def test_all_boards_have_eleven_slots():
    for formation in ALLOWED_FORMATIONS:
        board = TacticalBoard(formation)
        assert len(board.slots) == 11
        assert all(5 <= slot.x <= 95 and 5 <= slot.y <= 95 for slot in board.slots)


def test_formation_matchup_all_hundred_pairs_are_capped():
    values = [formation_matchup(a, b) for a in ALLOWED_FORMATIONS for b in ALLOWED_FORMATIONS]
    assert len(values) == 100
    assert all(0.92 <= value <= 1.08 for value in values)


def test_defensive_density_is_relative_and_bounded():
    values = [defensive_density(f) for f in ALLOWED_FORMATIONS]
    assert all(0.92 <= value <= 1.08 for value in values)
    assert defensive_density('5-3-2') > defensive_density('4-3-3')


def test_role_effective_stats_context():
    player = {'position': 'LW', 'passing': 75, 'defending': 50, 'shooting': 70, 'stamina': 80,
              'pace': 80, 'dribbling': 80, 'physical': 70, 'mental': 75}
    target = role_effective_stats(player, 'TARGET_MAN', 'LW')
    striker = role_effective_stats(player, 'TARGET_MAN', 'ST')
    assert target['aerial_effect'] < striker['aerial_effect']

    playmaker = role_effective_stats(player, 'PLAYMAKER', 'AM')
    assert playmaker['passing'] > player['passing']
    assert playmaker['defending'] < player['defending']


def test_instruction_effects():
    stats = {'attack': 100.0, 'defence': 100.0, 'stamina': 100.0}
    press = apply_instruction(stats, 'PRESS')
    stay = apply_instruction(stats, 'STAY_BACK')
    assert press['attack'] > stats['attack']
    assert press['stamina'] < stats['stamina']
    assert stay['attack'] < stats['attack']
    assert stay['defence'] > stats['defence']
    assert stay['stamina'] > stats['stamina']


def test_channel_matchup_433_vs_532():
    wide = channel_matchup(TacticalBoard('4-3-3'), TacticalBoard('5-3-2'))
    assert wide['CENTER'] < 1.0
    assert wide['LEFT'] > 1.0
    assert wide['RIGHT'] > 1.0
    assert count_channel_players(TacticalBoard('5-3-2'), 'CENTER') > count_channel_players(TacticalBoard('4-3-3'), 'CENTER')
