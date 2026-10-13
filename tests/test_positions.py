from app.game.positions import POSITION_LABELS_RU, POSITION_PENALTY, position_penalty


def test_position_penalty_matrix_examples():
    assert position_penalty('CB', 'ST') == 0.60
    assert position_penalty('ST', 'ST') == 0.00
    assert position_penalty('ST', 'GK') == 0.90
    assert POSITION_LABELS_RU['GK'] == 'ВРТ'
    assert len(POSITION_PENALTY) == 12
