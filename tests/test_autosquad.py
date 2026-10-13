from app.models import Player
from app.services import build_starting_lineup


def p(i, pos, secondary=(), **kw):
    base=dict(pace=75,shooting=75,passing=75,dribbling=75,defending=75,physical=75,stamina=75,mental=75)
    base.update(kw)
    return dict(id=i, position=pos, secondary_positions=list(secondary), potential=80, **base)


def test_autosquad_fills_eleven_slots_and_no_duplicates():
    players=[p(i+1,pos) for i,pos in enumerate([
        'GK','LB','CB','CB','RB','DM','CM','AM','LW','RW','ST'
    ])]
    lineup=build_starting_lineup(players,['GK','LB','CB','CB','RB','CM','CM','AM','LW','ST','RW'])
    assert len(lineup)==11
    assert len({x['id'] for x in lineup})==11


def test_autosquad_prefers_st_for_striker_and_secondary():
    players=[
        p(1,'CB',shooting=99,pace=99,physical=99),
        p(2,'ST',shooting=80,pace=80,physical=80),
        p(3,'CM',secondary=('ST',),shooting=78,pace=78,physical=78),
    ]
    lineup=build_starting_lineup(players,['ST','CB','CM'])
    assert lineup[0]['position']=='ST'

    players=[
        p(10,'CB',shooting=99,pace=99,physical=99),
        p(11,'CM',secondary=('ST',),shooting=78,pace=78,physical=78),
    ]
    lineup=build_starting_lineup(players,['ST','CB'])
    assert lineup[0]['id']==11
