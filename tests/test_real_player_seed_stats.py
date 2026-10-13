from app.real_player_stats import REAL_PLAYER_STATS


def test_real_player_stats_have_all_120_profiles():
    assert len(REAL_PLAYER_STATS) == 120
    assert set(REAL_PLAYER_STATS) == set(range(1, 121))


def test_real_player_stats_known_profiles():
    assert REAL_PLAYER_STATS[11]['pace'] == 85
    assert REAL_PLAYER_STATS[18]['defending'] == 95
    assert REAL_PLAYER_STATS[20]['passing'] == 91
    assert REAL_PLAYER_STATS[27]['passing'] == 95

