from app.game.substitutions import auto_substitution


def player(pid, pos='CB', stamina=80, fitness=100, potential=70):
    return type('P', (), dict(id=pid, position=pos, stamina=stamina, fitness=fitness, potential=potential, first_name=f'P{pid}', last_name='Test'))()


def test_injury_same_position_is_replaced():
    out=auto_substitution([player(1,'CB')],[player(11,'CB')],home_bench=[player(2,'CB',90)],away_bench=[player(12,'CB')],current_minute=46,home_injuries={1})
    assert len(out)==1 and out[0]['player_out'].id==1 and out[0]['player_in'].id==2


def test_red_card_is_never_replaced():
    out=auto_substitution([player(1,'CB')],[player(11,'CB')],home_bench=[player(2,'CB',90)],away_bench=[player(12,'CB')],current_minute=60,home_injuries={1},home_red_cards={1})
    assert out==[]


def test_fatigue_over_80_replaced():
    out=auto_substitution([player(1,'CB')],[player(11,'CB')],home_fatigue={1:81},away_fatigue={11:0},home_bench=[player(2,'CB',90)],away_bench=[player(12,'CB')],current_minute=70)
    assert len(out)==1 and out[0]['reason']=='усталость > 80'


def test_limit_five():
    out=auto_substitution([player(1,'CB')],[player(11,'CB')],home_fatigue={1:99},away_fatigue={11:99},home_bench=[player(2,'CB',90)],away_bench=[player(12,'CB',90)],current_minute=70,home_substitutions_used=5,away_substitutions_used=5)
    assert out==[]
