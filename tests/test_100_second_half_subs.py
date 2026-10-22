from app.game.engine import simulate_second_half
from app.game.tactics import Tactics, Style
from app.services import _dedupe_substitution_schedule

def p(i,pos='CB'):
    return type('P',(),dict(id=i,first_name=f'P{i}',last_name='Test',name=f'P{i}',position=pos,age=25,pace=70,shooting=70,passing=70,dribbling=70,defending=70,physical=70,stamina=70,mental=70,fitness=100,form=0,morale=70,talents=(),suspended=False))()

def test_100_second_half_runs_have_no_substitution_cycle():
    home=[p(i,'CB') for i in range(1,12)]; away=[p(i,'CB') for i in range(20,31)]
    bench=p(40,'CB')
    bad=[(46,1,bench,'HOME'),(46,40,p(41,'CB'),'HOME'),(46,41,p(1,'CB'),'HOME')]
    clean=_dedupe_substitution_schedule(home,away,bad)
    assert len(clean)==1
    t=Tactics('4-3-3',Style.BALANCE,50,50,50,50,50)
    for seed in range(100):
        _,_,events=simulate_second_half(home,away,t,t,seed,clean)
        assert len([e for e in events if e.type=='SUBSTITUTION'])<=1
