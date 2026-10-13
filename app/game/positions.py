POSITION_LABELS_RU = {
    'GK': 'ВРТ', 'LB': 'ЛЗ', 'CB': 'ЦЗ', 'RB': 'ПЗ',
    'DM': 'ОПЗ', 'CM': 'ЦПЗ', 'AM': 'АПЗ',
    'LW': 'ЛП', 'RW': 'ПП', 'ST': 'ЦФ',
    'LM': 'ЛПЗ', 'RM': 'ППЗ',
}

_POSITIONS = tuple(POSITION_LABELS_RU)

POSITION_PENALTY = {pos: {target: 0.0 for target in _POSITIONS} for pos in _POSITIONS}

for target in _POSITIONS:
    if target != 'GK':
        POSITION_PENALTY['GK'][target] = 0.90
        POSITION_PENALTY[target]['GK'] = 0.90


def _set(origin, **values):
    for target, penalty in values.items():
        POSITION_PENALTY[origin][target] = penalty

_set('LB', CB=0.15, RB=0.25, DM=0.25, CM=0.30, AM=0.40, LW=0.15, RW=0.30, ST=0.45)
_set('CB', LB=0.15, RB=0.15, DM=0.15, CM=0.30, AM=0.45, LW=0.40, RW=0.40, ST=0.60)
_set('RB', CB=0.15, LB=0.25, DM=0.25, CM=0.30, AM=0.40, RW=0.15, LW=0.30, ST=0.45)
_set('DM', CB=0.20, LB=0.25, RB=0.25, CM=0.15, AM=0.20, LW=0.35, RW=0.35, ST=0.50)
_set('CM', DM=0.15, AM=0.15, LB=0.30, RB=0.30, CB=0.30, LW=0.30, RW=0.30, ST=0.30)
_set('AM', CM=0.15, LW=0.20, RW=0.20, DM=0.25, ST=0.15, LB=0.45, RB=0.45, CB=0.45)
_set('LW', LB=0.20, AM=0.15, CM=0.30, RW=0.20, ST=0.25, DM=0.40, RB=0.40, CB=0.45)
_set('RW', RB=0.20, AM=0.15, CM=0.30, LW=0.20, ST=0.25, DM=0.40, LB=0.40, CB=0.45)
_set('ST', AM=0.15, LW=0.25, RW=0.25, CM=0.30, DM=0.50, LB=0.45, RB=0.45, CB=0.60)

# LM/RM are UI-only formation slots. Their nearest-wing profile mirrors LW/RW.
POSITION_PENALTY['LM'].update(POSITION_PENALTY['LW'])
POSITION_PENALTY['RM'].update(POSITION_PENALTY['RW'])
POSITION_PENALTY['LM']['LM'] = 0.0
POSITION_PENALTY['RM']['RM'] = 0.0


def position_penalty(from_pos: str, to_pos: str) -> float:
    """Return the single canonical positional penalty for a player assignment."""
    if from_pos == to_pos:
        return 0.0
    return float(POSITION_PENALTY.get(from_pos, {}).get(to_pos, 0.90 if to_pos == 'GK' or from_pos == 'GK' else 0.0))
