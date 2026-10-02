from app.game.scouting import build_scout_report
from app.game.engine import expected_goals
from app.game.tactics import Tactics
from app.models import Player


def p(i,pos,pace=75):
    return Player(i,'P',str(i),pos,25,pace,75,75,75,75,75,75,75)


def squad(start, slow_cb=False):
    positions=['GK','LB','CB','CB','RB','DM','CM','AM','LW','RW','ST']
    return [p(start+i,pos,55 if slow_cb and pos=='CB' and i==2 else 75) for i,pos in enumerate(positions)]


def test_scout_report_extracts_typical_shape_and_recommendation():
    report=build_scout_report(2,'Rivals',[
        {'formation':'4-3-3','style':'COUNTER','goals_for':2,'goals_against':1},
        {'formation':'4-3-3','style':'COUNTER','goals_for':2,'goals_against':2},
        {'formation':'4-4-2','style':'BALANCE','goals_for':0,'goals_against':2},
    ],{'Playmaker One':3,'Striker Two':2})
    assert report.typical_formation=='4-3-3'
    assert report.typical_style=='COUNTER'
    assert 'Playmaker One' in report.key_players
    assert report.counter_recommendations


def test_counter_plan_changes_expected_goals_when_target_matches():
    home=squad(1)
    away=squad(100,slow_cb=True)
    base=expected_goals(home,away,Tactics(),Tactics(),home_counter={'focus':'TARGET_SLOW_CB','intensity':100,'target_player_id':102})
    neutral=expected_goals(home,away,Tactics(),Tactics())
    assert base[0] > neutral[0]


def test_press_playmaker_requires_midfield_target():
    home=squad(1); away=squad(100)
    neutral=expected_goals(home,away,Tactics(),Tactics())
    marked=expected_goals(home,away,Tactics(),Tactics(),home_counter={'focus':'PRESS_PLAYMAKER','intensity':100,'target_player_id':107})
    assert marked[1] < neutral[1]
