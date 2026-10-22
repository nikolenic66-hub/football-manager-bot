"""Best-XI selection using formation slots and player OVR."""
from app.game.board import FORMATION_SLOTS
from app.game.ratings import player_rating


def _ovr(p):
    obj = type('BestXIPlayer', (), dict(p, suspended=False, form=int(p.get('form', 0) or 0), morale=int(p.get('morale', 70) or 70), fitness=int(p.get('fitness', 100) or 100)))()
    obj.position = str(p.get('position') or 'CM')
    return float(player_rating(obj))


def _fit(p, slot):
    pos = str(p.get('position') or '')
    secondary = str(p.get('secondary_positions') or '')
    if pos == slot or slot in secondary:
        return 1.0
    return 0.72


def get_best_xi(players, formation):
    if formation not in FORMATION_SLOTS:
        raise ValueError(f'Unknown formation: {formation}')
    available = [p for p in players if not p.get('is_injured') and not p.get('suspension_matches')]
    chosen = []
    for slot, _, _ in FORMATION_SLOTS[formation]:
        candidates = [p for p in available if int(p.get('id')) not in {int(x.get('id')) for x in chosen}]
        if not candidates:
            break
        pick = max(candidates, key=lambda p: (_ovr(p) * _fit(p, slot), _ovr(p), -int(p.get('id'))))
        chosen.append(pick)
    return chosen[:11]
