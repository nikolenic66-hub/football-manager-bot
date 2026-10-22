from app.game.player_comparison import compare_players
from app.game.player_history import format_trend


def _player(**extra):
    data={
        'pace':80,'shooting':80,'passing':80,'dribbling':80,
        'defending':70,'physical':80,'stamina':80,'mental':80,
        'position':'CM','fitness':100,'form':0,'morale':70,
        'potential':85,'market_value':1_000_000,'salary':50_000,'age':25,
    }
    data.update(extra)
    return data


def test_compare_players_returns_values_and_deltas():
    result=compare_players(_player(pace=92), _player(pace=89))
    assert result['player1']['ovr'] >= result['player2']['ovr']
    assert result['player1']['attributes']['pace'] == 92
    assert result['delta']['attributes']['pace'] == 3
    assert result['delta']['market_value'] == 0


def test_format_trend_compact_and_graph():
    short=[('2026-01-01',67,100000,70),('2027-01-01',72,120000,75)]
    assert format_trend(short) == '2026-01-01: 67 → 2027-01-01: 72'
    long=[(f'2026-01-{i:02d}',60+i,100000+i*1000,70) for i in range(1,5)]
    text=format_trend(long)
    assert text.startswith('61 ') and text.endswith('64') and ('█' in text or '▁' in text)



def test_compare_players_marks_zero_fitness_delta_as_unavailable():
    result=compare_players(_player(fitness=66), _player(fitness=0))
    assert result['delta']['fitness'] is None
