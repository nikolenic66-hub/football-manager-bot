from random import Random
from app.game.brilliance import brilliance_chance,roll_brilliance,brilliance_xg_bonus
from app.game.weather import generate_weather,weather_xg_modifier,weather_fatigue_multiplier
from app.game.ratings import effective_attributes,player_rating
from app.models import Player


def player(position='ST',fatigue=0):
    return Player(1,'Test','Player',position,25,90,90,85,88,70,85,90,85,fitness=100)


def test_brilliance_roll_is_rare_and_bounded():
    p=player()
    assert 0.01 <= brilliance_chance(p) <= 0.02
    assert 0.30 <= brilliance_xg_bonus(Random(1)) <= 0.50
    rng=Random(7)
    rolls=sum(roll_brilliance(p,rng) for _ in range(1000))
    assert 0 <= rolls <= 60


def test_weather_probabilities_and_modifiers():
    counts={x:0 for x in ('CLEAR','RAIN','WIND','SNOW','HEAT')}
    rng=Random(42)
    for _ in range(10000):
        counts[generate_weather(rng)] += 1
    assert counts['CLEAR'] > counts['RAIN'] > counts['WIND'] > counts['SNOW']
    assert weather_xg_modifier('RAIN')['passing'] == 0.92
    assert weather_xg_modifier('SNOW')['passing'] == 0.85
    assert weather_xg_modifier('WIND')['shooting'] == 0.90
    assert weather_xg_modifier('HEAT')['tempo'] == 0.95
    assert weather_fatigue_multiplier('SNOW') == 1.10
    assert weather_fatigue_multiplier('HEAT') == 1.15


def test_fatigue_is_attribute_based_and_position_specific():
    st=player('ST'); cb=player('CB')
    fresh=effective_attributes(st,0)
    tired=effective_attributes(st,100)
    tired_cb=effective_attributes(cb,100)
    assert tired['pace'] == 63.0
    assert tired['shooting'] == 72.0
    assert tired['passing'] == 72.25
    assert tired['defending'] == 64.4
    assert tired_cb['defending'] < tired['defending']
    assert player_rating(st) > player_rating(type('P',(),dict(st.__dict__,pace=tired['pace'],shooting=tired['shooting'],passing=tired['passing'],defending=tired['defending'],physical=tired['physical'],stamina=tired['stamina'],mental=tired['mental']))())
    assert fresh['pace'] == 90.0
