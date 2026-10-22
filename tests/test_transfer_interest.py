from app.game.transfer_interest import calculate_interest


def test_interest_is_always_bounded_for_100_cases():
    for i in range(100):
        player={
            'overall':50+i%51,'potential':50+(i*3)%51,'age':16+i%25,
            'salary':15_000+i*1_000,'market_value':500_000+i*25_000,'position':'ST',
        }
        club={
            'reputation':1+(i*7)%100,'budget':100_000+i*100_000,
            'league_tier':1+i%5,'offered_salary':15_000+i*5_000,'position_count':i%4,
        }
        score=calculate_interest(player,club)['interest_score']
        assert 0 <= score <= 100


def test_high_reputation_player_rejects_low_reputation_club():
    player={'overall':90,'potential':95,'age':27,'reputation':90,'salary':100_000,'market_value':3_500_000,'position':'ST'}
    result=calculate_interest(player,{'reputation':20,'budget':100_000_000,'league_tier':5,'offered_salary':120_000,'position_count':0})
    assert result['verdict']=='NOT_INTERESTED'
    assert 'Reputation too low' in result['reasons']


def test_high_salary_player_rejects_tiny_wage_offer():
    player={'overall':90,'potential':95,'age':27,'reputation':70,'salary':100_000,'market_value':3_500_000,'position':'ST'}
    result=calculate_interest(player,{'reputation':70,'budget':100_000_000,'league_tier':2,'offered_salary':20_000,'position_count':0})
    assert result['verdict']=='NOT_INTERESTED'
    assert 'Wage below expectations' in result['reasons']
