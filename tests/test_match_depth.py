from app.game.engine import simulate_second_half
from app.game.tactics import Tactics
from app.models import Player


def squad(start):
    pos=['GK','LB','CB','CB','RB','DM','CM','AM','LW','RW','ST']
    return [Player(start+i,'P',str(i),p,25,75,75,75,75,75,75,75,75) for i,p in enumerate(pos)]


def test_planned_substitution_is_recorded_in_second_half():
    home=squad(1); away=squad(100)
    bench=Player(999,'Bench','99','ST',24,90,88,80,86,50,78,90,82)
    hs,as_,events=simulate_second_half(home,away,Tactics(),Tactics(),321,[(60,home[-1].id,bench,'HOME')])
    subs=[e for e in events if e.type=='SUBSTITUTION']
    assert subs and subs[0].minute==60 and subs[0].player==bench.name


def test_second_half_has_match_incidents():
    home=squad(1); away=squad(100)
    found=False
    for seed in range(200):
        events=simulate_second_half(home,away,Tactics(),Tactics(),seed)[2]
        if any(e.type in {'YELLOW','RED','INJURY'} for e in events):
            found=True
            break
    assert found


def test_visual_match_events_have_deterministic_coordinates_and_types():
    from app.game.engine import simulate_first_half
    home=squad(1); away=squad(100)
    from app.game.tactics import Tactics
    a=simulate_first_half(home,away,Tactics(),Tactics(),777)
    b=simulate_first_half(home,away,Tactics(),Tactics(),777)
    assert a[2]==b[2]
    visual=[e for e in a[2] if e.metadata.get('visual_type')]
    assert visual
    assert any(e.type=='PASS' for e in visual)
    assert any(e.type=='SHOT' for e in visual)
    for e in visual:
        start=e.metadata['start']; end=e.metadata.get('end')
        assert 0 <= start[0] <= 100 and 0 <= start[1] <= 100
        if end:
            assert 0 <= end[0] <= 100 and 0 <= end[1] <= 100


def test_goal_shot_points_toward_correct_goal():
    from app.game.engine import simulate_match
    from app.game.tactics import Tactics
    r=simulate_match('HOME','AWAY',squad(1),squad(100),Tactics(),Tactics(),123)
    goals=[e for e in r.events if e.type=='SHOT' and e.metadata.get('result')=='GOAL']
    for e in goals:
        end=e.metadata['end']
        assert end[0] in (0,100)
        if e.team=='HOME': assert end[0]==100
        if e.team=='AWAY': assert end[0]==0
