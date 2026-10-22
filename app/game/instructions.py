"""Structural effects for individual tactical instructions."""
from copy import deepcopy

INSTRUCTION_EFFECTS = {
    'BALANCED': {'attack':0.00,'defence':0.00,'stamina':0.00,'width':0.00,'risk':0.00},
    'STAY_BACK': {'attack':-0.06,'defence':0.06,'stamina':0.03,'width':-0.01,'risk':-0.05},
    'OVERLAP': {'attack':0.06,'defence':-0.03,'stamina':-0.05,'width':0.08,'risk':0.06},
    'INVERT': {'attack':0.02,'defence':0.03,'stamina':-0.01,'width':-0.06,'risk':-0.01},
    'ROAM': {'attack':0.04,'defence':-0.02,'stamina':-0.03,'width':0.02,'risk':0.04},
    'HOLD_POSITION': {'attack':-0.02,'defence':0.04,'stamina':0.02,'width':0.00,'risk':-0.03},
    'GET_FORWARD': {'attack':0.07,'defence':-0.04,'stamina':-0.04,'width':0.02,'risk':0.06},
    'PRESS': {'attack':0.04,'defence':0.03,'stamina':-0.07,'width':0.00,'risk':0.04},
    'CONSERVE': {'attack':-0.04,'defence':0.02,'stamina':0.06,'width':0.00,'risk':-0.05},
    'MARK': {'attack':-0.01,'defence':0.06,'stamina':-0.03,'width':0.00,'risk':-0.02},
    'CUT_INSIDE': {'attack':0.05,'defence':-0.01,'stamina':-0.02,'width':-0.07,'risk':0.03},
    'STAY_WIDE': {'attack':0.04,'defence':-0.01,'stamina':-0.02,'width':0.08,'risk':0.02},
    'TARGET': {'attack':0.05,'defence':-0.01,'stamina':-0.02,'width':0.01,'risk':0.02},
}


def apply_instruction(stats, instruction):
    if instruction not in INSTRUCTION_EFFECTS:
        raise ValueError(f'Unsupported instruction: {instruction}')
    out = deepcopy(stats)
    effect = INSTRUCTION_EFFECTS[instruction]
    for key, value in effect.items():
        base = float(out.get(key, 0.0) or 0.0)
        out[key] = base * (1.0 + value) if key in {'attack','defence','stamina','width'} else base + value
    return out
