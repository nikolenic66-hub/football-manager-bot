from app.game.club_management import (
    age_factor, development_points, attribute_growth, training_result,
    contract_offer, transfer_quote, morale_after_match, practice_minutes,
)


def test_young_players_develop_more_than_old_players():
    young=development_points(19,90,72,'TECHNICAL','NORMAL',900,80,8)
    veteran=development_points(33,90,72,'TECHNICAL','NORMAL',900,80,8)
    assert young>veteran
    assert age_factor(19)>age_factor(28)>age_factor(33)


def test_training_focus_targets_attributes():
    shooting=attribute_growth(70,90,2,'ATTACK','shooting')
    defending=attribute_growth(70,90,2,'ATTACK','defending')
    assert shooting>defending
    assert attribute_growth(90,90,5,'ATTACK','shooting')==90


def test_high_intensity_costs_morale_and_adds_risk():
    normal=training_result(21,90,70,'BALANCED','NORMAL',900,80,10)
    high=training_result(21,90,70,'PHYSICAL','HIGH',900,80,10)
    assert high.injury_risk>normal.injury_risk
    assert high.development_points>=normal.development_points


def test_contract_offer_is_bounded_and_progressive():
    young=contract_offer(10_000_000,82,20,4,'KEY')
    veteran=contract_offer(10_000_000,82,31,4,'KEY')
    assert young.salary>0 and young.release_clause>=20_000_000
    assert veteran.salary!=young.salary
    assert 1<=young.years<=5


def test_transfer_quote_reflects_age_and_form():
    young=transfer_quote(10_000_000,20,2,3)
    old=transfer_quote(10_000_000,32,-2,1)
    assert young.asking_price>old.asking_price
    assert young.minimum_price<=young.asking_price


def test_morale_responds_to_minutes_and_result():
    starter_win=morale_after_match(70,90,7.5,'WIN','STAR',True)
    bench_loss=morale_after_match(70,15,5.9,'LOSS','STAR',False)
    assert starter_win>70
    assert bench_loss<70


def test_practice_minutes_is_deterministic():
    assert practice_minutes(10,900,8)==28
    assert practice_minutes(0,0,0)==0

def test_training_intensity_order_is_stable():
    light=training_result(20,90,70,'TECHNICAL','LIGHT',900,80,8)
    normal=training_result(20,90,70,'TECHNICAL','NORMAL',900,80,8)
    high=training_result(20,90,70,'TECHNICAL','HIGH',900,80,8)
    assert light.development_points <= normal.development_points <= high.development_points
    assert light.injury_risk < normal.injury_risk < high.injury_risk
