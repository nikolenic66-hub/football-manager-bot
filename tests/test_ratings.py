from app.models import Player
from app.game.ratings import player_rating

def p(i,pos,**kw):
    base=dict(pace=75,shooting=75,passing=75,dribbling=75,defending=75,physical=75,stamina=75,mental=75)
    base.update(kw)
    return Player(i,'Test',str(i),pos,25,**base)

def test_rating_range():
    assert 1 <= player_rating(p(1,'ST')) <= 100

def test_position_changes_rating_for_specialist_profile():
    gk=p(1,'GK',defending=90,mental=90,shooting=45)
    st=p(2,'ST',defending=45,mental=60,shooting=90)
    assert player_rating(gk) != player_rating(st)
