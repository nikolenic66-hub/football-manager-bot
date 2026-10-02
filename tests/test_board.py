from app.game.board import TacticalBoard, board_effect
from app.game.tactics import Tactics, Style
from app.game.engine import expected_goals
from app.models import Player

def squad(start):
    pos=['GK','LB','CB','CB','RB','DM','CM','AM','LW','RW','ST']
    return [Player(start+i,'P',str(i),p,25,75,75,75,75,75,75,75,75) for i,p in enumerate(pos)]

def test_board_has_eleven_slots_and_reposition():
    b=TacticalBoard('4-3-3').assign(8, 'WINGER','STAY_WIDE').reposition(8,70,10)
    assert len(b.slots)==11 and b.slots[8].instruction=='STAY_WIDE' and b.slots[8].x==70

def test_board_shape_changes_effect():
    a=TacticalBoard('4-3-3')
    b=a.assign(5,'INVERTED','INVERT').assign(6,'ROAMER','ROAM').reposition(8,80,10)
    assert board_effect(a,Style.BALANCE,50,50,50) != board_effect(b,Style.BALANCE,50,50,50)

def test_board_can_change_matchup():
    h=squad(1); a=squad(100); t=Tactics()
    narrow=TacticalBoard('4-3-3')
    wide=TacticalBoard('4-3-3').reposition(8,78,8).reposition(10,78,92).assign(1,'FULLBACK','OVERLAP').assign(4,'FULLBACK','OVERLAP')
    assert expected_goals(h,a,t,t,narrow,narrow) != expected_goals(h,a,t,t,wide,narrow)

def test_formation_roles_are_used_as_match_roles():
    from app.game.board import FORMATION_SLOTS
    assert [x[0] for x in FORMATION_SLOTS['4-3-3']] == ['GK','LB','CB','CB','RB','CM','DM','CM','LW','ST','RW']
    assert [x[0] for x in FORMATION_SLOTS['4-2-3-1']] == ['GK','LB','CB','CB','RB','DM','DM','LW','AM','RW','ST']
