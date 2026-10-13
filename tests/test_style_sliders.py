from app.game.tactics import Style, STYLE_SLIDERS


def test_style_slider_defaults():
    assert STYLE_SLIDERS[Style.ATTACK] == (70,60,65,70,55)
    assert STYLE_SLIDERS[Style.DEFENSE] == (40,45,40,30,45)
    assert STYLE_SLIDERS[Style.COUNTER] == (65,30,45,25,50)
    assert STYLE_SLIDERS[Style.POSSESSION] == (45,70,60,60,40)
    assert STYLE_SLIDERS[Style.BALANCE] == (50,50,50,50,50)
