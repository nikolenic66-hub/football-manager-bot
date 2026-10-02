TALENT_EFFECTS = {
    'SWEEPER_KEEPER': {'build_up': .035, 'high_line': .045}, 'SHOT_STOPPER': {'shot_save': .045},
    'AERIAL_COMMANDER': {'set_piece_def': .05}, 'DISTRIBUTOR': {'build_up': .045, 'possession': .035},
    'BALL_PLAYING_DEFENDER': {'build_up': .04, 'press_resistance': .035}, 'STOPPER': {'def_duel': .05, 'high_line': -.02},
    'AERIAL_BEAST': {'set_piece_def': .055, 'set_piece_att': .025}, 'RECOVERY_DEFENDER': {'transition_def': .055},
    'LEADER': {'composure': .035, 'team_stability': .025}, 'OVERLAP_RUNNER': {'width_attack': .055, 'stamina_cost': .02},
    'INVERTED_FULLBACK': {'midfield_control': .05, 'width_attack': -.015}, 'CROSSER': {'crossing': .06},
    'ONE_V_ONE_DEFENDER': {'wide_def': .06}, 'ANCHOR': {'central_def': .06}, 'BALL_WINNER': {'pressing': .05, 'foul_risk': .02},
    'DEEP_PLAYMAKER': {'build_up': .055, 'tempo_control': .045}, 'PRESS_RESISTANT': {'press_resistance': .065},
    'DESTROYER': {'pressing': .045, 'foul_risk': .035}, 'BOX_TO_BOX': {'transition': .05, 'stamina': .035},
    'CARRIER': {'transition': .055, 'dribble_transition': .04}, 'TEMPO_CONTROLLER': {'tempo_control': .06},
    'PLAYMAKER': {'chance_creation': .07}, 'THROUGH_BALLER': {'chance_creation': .075, 'counter': .025},
    'SECOND_STRIKER': {'box_presence': .055}, 'PRESSING_10': {'pressing': .055},
    'CREATIVE_ENGINE': {'chance_creation': .065, 'possession': .035}, 'INSIDE_FORWARD': {'box_presence': .06, 'width_attack': -.01},
    'WINGER': {'width_attack': .065, 'crossing': .04}, 'CREATIVE_DRIBBLER': {'dribble_transition': .07},
    'COUNTER_RUNNER': {'counter': .07}, 'PRESSER': {'pressing': .055}, 'POACHER': {'finishing': .075, 'box_presence': .055},
    'TARGET_MAN': {'aerial_attack': .07, 'hold_up': .06}, 'PRESSING_FORWARD': {'pressing': .07},
    'FALSE_NINE': {'build_up': .05, 'chance_creation': .045, 'box_presence': -.025}, 'COMPLETE_FORWARD': {'finishing': .04, 'chance_creation': .04, 'pressing': .03},
}

def player_talent_bonus(player, key):
    return sum(TALENT_EFFECTS.get(t, {}).get(key, 0.0) for t in (getattr(player, 'talents', ()) or ()))

def talent_bonus(players, key):
    return sum(player_talent_bonus(p, key) for p in players)
