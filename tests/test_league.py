from app.game.league import round_robin
def test_eight_teams():
    fs=round_robin(list(range(1,9)),True);assert len(fs)==56
    counts={}
    for f in fs:
        k=tuple(sorted((f.home_club_id,f.away_club_id)));counts[k]=counts.get(k,0)+1
    assert len(counts)==28 and set(counts.values())=={2}
