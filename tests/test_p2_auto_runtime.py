from app.game.substitutions import auto_substitution
from app.services import _dedupe_substitution_schedule

def p(pid,pos='CB',stamina=80,fitness=100,potential=70):
    return type('P',(),dict(id=pid,position=pos,stamina=stamina,fitness=fitness,potential=potential,first_name=f'P{pid}',last_name='Test'))()

def test_100_auto_substitution_cases_and_limits():
    for _ in range(100):
        home=[p(1,'CB'),p(3,'ST')]; away=[p(11,'CB'),p(13,'ST')]
        home_bench=[p(2,'CB',90),p(4,'ST',90)]; away_bench=[p(12,'CB',90),p(14,'ST',90)]
        out=auto_substitution(home,away,{1:81,3:82},{11:81,13:82},home_bench=home_bench,away_bench=away_bench,current_minute=60)
        assert len(out)==2
        assert all(x['reason']=='усталость > 80' for x in out)
        assert all(x['player_out'].position==x['player_in'].position for x in out)

def test_100_cycle_schedules_are_stable():
    home=[p(1),p(2),p(3)]; away=[p(11),p(12),p(13)]
    for _ in range(100):
        bad=[(46,1,p(4),'HOME'),(46,4,p(5),'HOME'),(46,5,p(1),'HOME')]
        accepted=_dedupe_substitution_schedule(home,away,bad)
        assert [(x[1],x[2].id) for x in accepted] == [(1,4)]
