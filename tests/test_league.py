from app.game.league import round_robin

def test_sixteen_teams_double_round():
    fs=round_robin(list(range(1,17)),True)
    assert len(fs)==240
    assert max(f.round_no for f in fs)==30
    pairs={}
    for f in fs:
        k=tuple(sorted((f.home_club_id,f.away_club_id)))
        pairs[k]=pairs.get(k,0)+1
    assert len(pairs)==120 and set(pairs.values())=={2}
