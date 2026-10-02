from app.models import Player
from app.game.engine import simulate_match
from app.game.tactics import Tactics,Style
def p(i,pos,b): return Player(i,'P',str(i),pos,25,b,b,b,b,b,b,b,b)
def squad(start,b=75): return [p(start+i,pos,b+((i*3)%5-2)) for i,pos in enumerate(['GK','LB','CB','CB','RB','DM','CM','AM','LW','RW','ST'])]
def test_reproducible():
    r1=simulate_match('H','A',squad(1),squad(100),Tactics(),Tactics(),12345);r2=simulate_match('H','A',squad(1),squad(100),Tactics(),Tactics(),12345)
    assert (r1.home_score,r1.away_score,r1.events,r1.ratings)==(r2.home_score,r2.away_score,r2.events,r2.ratings)
def test_nonnegative():
    r=simulate_match('H','A',squad(1),squad(100),Tactics(),Tactics(),7);assert r.home_score>=0 and r.away_score>=0 and r.home_xg>0 and r.away_xg>0
def test_upset_possible():
    strong=squad(1,88);weak=squad(100,65);out=set()
    for seed in range(100):
        r=simulate_match('S','W',strong,weak,Tactics(style=Style.ATTACK),Tactics(style=Style.DEFENSE),seed);out.add((r.home_score,r.away_score))
    assert len(out)>1
from app.game.engine import simulate_first_half, simulate_second_half, expected_goals

def test_talent_changes_counter_matchup():
    plain=squad(1,75)
    talented=[Player(p.id,p.first_name,p.last_name,p.position,p.age,p.pace,p.shooting,p.passing,p.dribbling,p.defending,p.physical,p.stamina,p.mental,p.fitness,p.form,p.suspended,('COUNTER_RUNNER',) if p.position=='ST' else ()) for p in plain]
    t= Tactics(style=Style.COUNTER, defensive_line=80)
    d= Tactics(style=Style.DEFENSE, defensive_line=75)
    assert expected_goals(talented,plain,t,d)[0] > expected_goals(plain,plain,t,d)[0]

def test_halves_are_reproducible_and_second_half_changes_with_tactics():
    h=squad(1); a=squad(100)
    ht=Tactics(); at=Tactics()
    a1=simulate_first_half(h,a,ht,at,100)
    a2=simulate_first_half(h,a,ht,at,100)
    assert (a1[0],a1[1],a1[2])==(a2[0],a2[1],a2[2])
    attack_t=Tactics(style=Style.ATTACK,tempo=95,pressing=80,width=75,defensive_line=70,aggression=60)
    assert expected_goals(h,a,ht,at) != expected_goals(h,a,attack_t,at)


def test_phase_matchup_changes_with_pressing_and_high_line():
    from app.game.engine import expected_goals
    from app.game.tactics import Tactics, Style
    from app.game.board import TacticalBoard
    h=squad(1); a=squad(100)
    counter=Tactics(style=Style.COUNTER,defensive_line=50)
    high_press=Tactics(style=Style.POSSESSION,pressing=90,defensive_line=80)
    low_press=Tactics(style=Style.POSSESSION,pressing=30,defensive_line=45)
    x_high=expected_goals(h,a,counter,high_press,TacticalBoard('4-3-3'),TacticalBoard('4-3-3'))
    x_low=expected_goals(h,a,counter,low_press,TacticalBoard('4-3-3'),TacticalBoard('4-3-3'))
    assert x_high != x_low
