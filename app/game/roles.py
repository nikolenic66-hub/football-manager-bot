"""Structural role profiles for iteration 1.

These profiles describe intended effects only; the match engine does not yet
apply full role logic.
"""
from copy import deepcopy

ROLE_PROFILES = {
    'SWEEPER_KEEPER': {'positions': {'GK'}, 'attack': .03, 'defence': .03, 'passing': .06, 'stamina': -.01, 'aerial': 0.0},
    'SHOT_STOPPER': {'positions': {'GK'}, 'attack': 0.0, 'defence': .06, 'passing': -.02, 'stamina': 0.0, 'aerial': .02},
    'AERIAL_COMMANDER': {'positions': {'GK'}, 'attack': 0.0, 'defence': .05, 'passing': .01, 'stamina': 0.0, 'aerial': .08},
    'DISTRIBUTOR': {'positions': {'GK'}, 'attack': .02, 'defence': .01, 'passing': .08, 'stamina': -.01, 'aerial': 0.0},
    'BALL_PLAYING_DEFENDER': {'positions': {'CB','LB','RB'}, 'attack': .02, 'defence': .01, 'passing': .07, 'stamina': -.01, 'aerial': .02},
    'STOPPER': {'positions': {'CB'}, 'attack': -.01, 'defence': .07, 'passing': -.02, 'stamina': -.02, 'aerial': .03},
    'AERIAL_BEAST': {'positions': {'CB'}, 'attack': .01, 'defence': .05, 'passing': -.01, 'stamina': 0.0, 'aerial': .10},
    'RECOVERY_DEFENDER': {'positions': {'CB','LB','RB'}, 'attack': .01, 'defence': .04, 'passing': 0.0, 'stamina': .02, 'aerial': .01},
    'LEADER': {'positions': {'CB','DM','CM'}, 'attack': .01, 'defence': .04, 'passing': .02, 'stamina': .01, 'aerial': 0.0},
    'OVERLAP_RUNNER': {'positions': {'LB','RB','LM','RM'}, 'attack': .06, 'defence': -.02, 'passing': .03, 'stamina': -.04, 'aerial': 0.0},
    'INVERTED_FULLBACK': {'positions': {'LB','RB'}, 'attack': .02, 'defence': .03, 'passing': .06, 'stamina': -.01, 'aerial': 0.0},
    'CROSSER': {'positions': {'LB','RB','LM','RM','LW','RW'}, 'attack': .04, 'defence': -.01, 'passing': .08, 'stamina': -.01, 'aerial': .01},
    'ONE_V_ONE_DEFENDER': {'positions': {'LB','RB','CB'}, 'attack': -.01, 'defence': .07, 'passing': 0.0, 'stamina': -.01, 'aerial': .01},
    'ANCHOR': {'positions': {'DM'}, 'attack': -.02, 'defence': .08, 'passing': .03, 'stamina': .02, 'aerial': 0.0},
    'BALL_WINNER': {'positions': {'DM','CM'}, 'attack': .01, 'defence': .07, 'passing': -.01, 'stamina': -.03, 'aerial': 0.0},
    'DEEP_PLAYMAKER': {'positions': {'DM','CM'}, 'attack': .02, 'defence': .01, 'passing': .09, 'stamina': -.01, 'aerial': 0.0},
    'PRESS_RESISTANT': {'positions': {'DM','CM','AM'}, 'attack': .03, 'defence': .01, 'passing': .06, 'stamina': -.01, 'aerial': 0.0},
    'DESTROYER': {'positions': {'DM','CM'}, 'attack': -.01, 'defence': .08, 'passing': -.03, 'stamina': -.03, 'aerial': 0.0},
    'BOX_TO_BOX': {'positions': {'CM'}, 'attack': .04, 'defence': .04, 'passing': .02, 'stamina': -.05, 'aerial': .01},
    'CARRIER': {'positions': {'CM','AM'}, 'attack': .05, 'defence': .01, 'passing': .03, 'stamina': -.03, 'aerial': 0.0},
    'TEMPO_CONTROLLER': {'positions': {'CM','DM'}, 'attack': .02, 'defence': .01, 'passing': .08, 'stamina': -.01, 'aerial': 0.0},
    'PLAYMAKER': {'positions': {'CM','AM'}, 'attack': .06, 'defence': -.05, 'passing': .10, 'stamina': -.01, 'aerial': 0.0},
    'THROUGH_BALLER': {'positions': {'AM','CM'}, 'attack': .07, 'defence': -.03, 'passing': .10, 'stamina': -.01, 'aerial': 0.0},
    'SECOND_STRIKER': {'positions': {'AM','ST'}, 'attack': .07, 'defence': -.02, 'passing': .03, 'stamina': -.02, 'aerial': .01},
    'PRESSING_10': {'positions': {'AM'}, 'attack': .04, 'defence': .03, 'passing': .02, 'stamina': -.05, 'aerial': 0.0},
    'CREATIVE_ENGINE': {'positions': {'AM','CM'}, 'attack': .08, 'defence': -.04, 'passing': .08, 'stamina': -.02, 'aerial': 0.0},
    'INSIDE_FORWARD': {'positions': {'LW','RW'}, 'attack': .08, 'defence': -.03, 'passing': .03, 'stamina': -.02, 'aerial': .01},
    'WINGER': {'positions': {'LW','RW','LM','RM'}, 'attack': .06, 'defence': -.01, 'passing': .05, 'stamina': -.02, 'aerial': .01},
    'CREATIVE_DRIBBLER': {'positions': {'LW','RW','AM'}, 'attack': .08, 'defence': -.03, 'passing': .03, 'stamina': -.03, 'aerial': 0.0},
    'COUNTER_RUNNER': {'positions': {'LW','RW','ST','AM'}, 'attack': .08, 'defence': -.02, 'passing': .02, 'stamina': -.04, 'aerial': .01},
    'PRESSER': {'positions': {'LW','RW','ST','AM'}, 'attack': .03, 'defence': .04, 'passing': 0.0, 'stamina': -.06, 'aerial': 0.0},
    'POACHER': {'positions': {'ST'}, 'attack': .10, 'defence': -.04, 'passing': -.01, 'stamina': -.01, 'aerial': .02},
    'TARGET_MAN': {'positions': {'ST'}, 'attack': .07, 'defence': -.02, 'passing': .02, 'stamina': -.02, 'aerial': .10},
    'PRESSING_FORWARD': {'positions': {'ST'}, 'attack': .05, 'defence': .04, 'passing': .01, 'stamina': -.06, 'aerial': .01},
    'FALSE_NINE': {'positions': {'ST'}, 'attack': .05, 'defence': .01, 'passing': .07, 'stamina': -.02, 'aerial': -.02},
    'COMPLETE_FORWARD': {'positions': {'ST'}, 'attack': .07, 'defence': .02, 'passing': .04, 'stamina': -.03, 'aerial': .04},
}


def _stats(player):
    if isinstance(player, dict):
        return {k: float(player.get(k, 0) or 0) for k in ('pace','shooting','passing','dribbling','defending','physical','stamina','mental')}
    return {k: float(getattr(player, k, 0) or 0) for k in ('pace','shooting','passing','dribbling','defending','physical','stamina','mental')}


def role_effective_stats(player, role, position=None):
    """Return normalized role-adjusted stats without changing the player."""
    profile = ROLE_PROFILES.get(role)
    if profile is None:
        raise ValueError(f'Unsupported role: {role}')
    stats = _stats(player)
    position = position or (player.get('position') if isinstance(player, dict) else getattr(player, 'position', None))
    fit = 1.0 if position in profile['positions'] else .72
    stats['passing'] *= 1 + profile['passing'] * fit
    stats['defending'] *= 1 + profile['defence'] * fit
    stats['shooting'] *= 1 + profile['attack'] * fit
    stats['stamina'] *= 1 + profile['stamina'] * fit
    stats['aerial_effect'] = profile['aerial'] * fit
    stats['role_fit'] = fit
    return stats
