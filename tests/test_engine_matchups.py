from app.models import Player
from app.game.engine import expected_goals, simulate_match
from app.game.tactics import Tactics, Style
from app.game.board import TacticalBoard

POSITIONS = ['GK','LB','CB','CB','RB','DM','CM','AM','LW','RW','ST']


def squad(start, base=75):
    return [Player(start+i, 'P', str(start+i), pos, 25,
                   base, base, base, base, base, base, base, base)
            for i, pos in enumerate(POSITIONS)]


def test_home_advantage_produces_home_win_rate_above_45_percent():
    home = squad(1, 75)
    away = squad(100, 75)
    ht = Tactics()
    at = Tactics()
    wins = 0
    for seed in range(100):
        result = simulate_match('H', 'A', home, away, ht, at, seed,
                                TacticalBoard(ht.formation), TacticalBoard(at.formation))
        if result.home_score > result.away_score:
            wins += 1
    assert wins / 100 > 0.45


def test_formation_matchup_gives_five_defenders_better_resistance():
    attacking_side = squad(1, 75)
    fixed_opponent = squad(100, 75)
    four_three_three = Tactics(formation='4-3-3')
    five_three_two = Tactics(formation='5-3-2')
    opponent_tactics = Tactics(formation='4-3-3')
    xg_433 = expected_goals(attacking_side, fixed_opponent, four_three_three, opponent_tactics,
                            TacticalBoard('4-3-3'), TacticalBoard('4-3-3'))
    xg_532 = expected_goals(attacking_side, fixed_opponent, five_three_two, opponent_tactics,
                            TacticalBoard('5-3-2'), TacticalBoard('4-3-3'))
    assert xg_532[0] < xg_433[0]
    # Against the same 4-3-3 attacker, the 5-3-2 defensive block concedes less xG.
    defensive_532 = expected_goals(fixed_opponent, attacking_side, opponent_tactics, five_three_two,
                                   TacticalBoard('4-3-3'), TacticalBoard('5-3-2'))[0]
    defensive_433 = expected_goals(fixed_opponent, attacking_side, opponent_tactics, four_three_three,
                                   TacticalBoard('4-3-3'), TacticalBoard('4-3-3'))[0]
    assert defensive_532 < defensive_433


def test_position_channel_strong_left_winger_exploits_weak_right_back():
    attack = squad(1, 75)
    defense = squad(100, 75)
    ht = Tactics(formation='4-3-3')
    at = Tactics(formation='4-3-3')
    hb = TacticalBoard('4-3-3')
    ab = TacticalBoard('4-3-3')

    baseline = expected_goals(attack, defense, ht, at, hb, ab)[0]

    # LW 90 versus RB 60 should improve the attacking channel relative to baseline.
    strong_lw = list(attack)
    lw = strong_lw[8]
    strong_lw[8] = Player(lw.id, lw.first_name, lw.last_name, lw.position, lw.age,
                          90, 90, 90, 90, 75, 75, 75, 75)
    weak_rb = list(defense)
    rb = weak_rb[4]
    weak_rb[4] = Player(rb.id, rb.first_name, rb.last_name, rb.position, rb.age,
                        60, 60, 60, 60, 60, 60, 60, 60)
    exploited = expected_goals(strong_lw, weak_rb, ht, at, hb, ab)[0]
    assert exploited > baseline
