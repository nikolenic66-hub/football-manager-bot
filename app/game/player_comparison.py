"""Player comparison helpers."""
from __future__ import annotations

from app.game.ratings import player_rating


ATTRIBUTE_FIELDS = (
    'pace', 'shooting', 'passing', 'dribbling',
    'defending', 'physical', 'stamina', 'mental',
)
SCALAR_FIELDS = ('potential', 'market_value', 'salary', 'age', 'fitness', 'form', 'morale')


def _get(player, key, default=None):
    if isinstance(player, dict):
        return player.get(key, default)
    return getattr(player, key, default)


def _overall(player):
    explicit = _get(player, 'overall')
    if explicit is not None:
        return int(round(float(explicit)))
    attrs = {name: int(_get(player, name, 0) or 0) for name in ATTRIBUTE_FIELDS}
    obj = type('ComparisonPlayer', (), attrs)()
    obj.position = str(_get(player, 'position', 'CM') or 'CM')
    obj.fitness = int(_get(player, 'fitness', 100) or 100)
    obj.form = int(_get(player, 'form', 0) or 0)
    obj.morale = int(_get(player, 'morale', 70) or 70)
    return int(round(float(player_rating(obj))))


def compare_players(player1, player2) -> dict:
    """Return comparable values and player2-relative deltas (player1 - player2)."""
    def pack(player):
        values = {'ovr': _overall(player)}
        for field in SCALAR_FIELDS:
            values[field] = int(_get(player, field, 0) or 0)
        values['attributes'] = {field: int(_get(player, field, 0) or 0) for field in ATTRIBUTE_FIELDS}
        values['development_trend'] = _get(player, 'development_trend', '') or ''
        return values

    left, right = pack(player1), pack(player2)
    deltas = {'ovr': left['ovr'] - right['ovr']}
    for field in SCALAR_FIELDS:
        if field == 'fitness' and (left[field] <= 0 or right[field] <= 0):
            deltas[field] = None
        else:
            deltas[field] = left[field] - right[field]
    deltas['attributes'] = {
        field: left['attributes'][field] - right['attributes'][field]
        for field in ATTRIBUTE_FIELDS
    }
    return {'player1': left, 'player2': right, 'delta': deltas}
