import pytest
from app.game.tactics import Tactics,Style,tactical_modifiers
def test_attack(): assert tactical_modifiers(Tactics(style=Style.ATTACK))[0]>tactical_modifiers(Tactics())[0]
def test_invalid():
    with pytest.raises(ValueError): Tactics(formation='2-2-6')
