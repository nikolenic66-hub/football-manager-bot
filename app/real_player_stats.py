"""Legacy import path for deterministic fictional player stat profiles.

The historical real-athlete rating table has been removed. These values are
synthetic and are not intended to represent any real player.
"""
from random import Random

_ATTRIBUTES = ('pace', 'shooting', 'passing', 'dribbling', 'defending', 'physical', 'stamina', 'mental')
_rng = Random(20261020)
FICTIONAL_PLAYER_STATS = {
    player_id: {attribute: _rng.randint(52, 94) for attribute in _ATTRIBUTES}
    for player_id in range(1, 121)
}
# Backward-compatible alias for downstream scripts that still import the old name.
REAL_PLAYER_STATS = FICTIONAL_PLAYER_STATS
