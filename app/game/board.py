from dataclasses import dataclass, field

@dataclass(frozen=True)
class PlayerSlot:
    slot: str
    x: int
    y: int
    role: str = 'STARTER'
    instruction: str = 'BALANCED'

# Coordinates are always image/field coordinates: x = lateral (left→right),
# y = depth (own goal at 0, opponent goal at 100).  The same coordinates are
# consumed by the engine and visual renderers; away sides mirror only the y axis.
FORMATION_SLOTS = {
    '4-3-3': [('GK',50,8),('LB',15,25),('CB',38,25),('CB',62,25),('RB',85,25),('CM',30,48),('DM',50,45),('CM',70,48),('LW',15,72),('ST',50,82),('RW',85,72)],
    '4-4-2': [('GK',50,8),('LB',15,25),('CB',38,25),('CB',62,25),('RB',85,25),('LM',15,48),('CM',38,48),('CM',62,48),('RM',85,48),('ST',40,80),('ST',60,80)],
    '4-2-3-1': [('GK',50,8),('LB',15,25),('CB',38,25),('CB',62,25),('RB',85,25),('DM',40,45),('DM',60,45),('LW',18,67),('AM',50,67),('RW',82,67),('ST',50,82)],
    '3-5-2': [('GK',50,8),('CB',30,25),('CB',50,25),('CB',70,25),('LM',10,48),('CM',35,48),('DM',50,45),('CM',65,48),('RM',90,48),('ST',40,80),('ST',60,80)],
    '5-3-2': [('GK',50,8),('LB',10,25),('CB',30,25),('CB',50,25),('CB',70,25),('RB',90,25),('CM',32,50),('DM',50,48),('CM',68,50),('ST',40,80),('ST',60,80)],
}

VALID_INSTRUCTIONS = {'BALANCED','STAY_BACK','OVERLAP','INVERT','ROAM','HOLD_POSITION','GET_FORWARD','PRESS','CONSERVE','MARK','CUT_INSIDE','STAY_WIDE','TARGET'}

@dataclass(frozen=True)
class TacticalBoard:
    formation: str = '4-3-3'
    slots: tuple[PlayerSlot, ...] = field(default_factory=tuple)
    def __post_init__(self):
        if self.formation not in FORMATION_SLOTS:
            raise ValueError('Unsupported formation')
        if not self.slots:
            object.__setattr__(self, 'slots', tuple(PlayerSlot(slot,x,y) for slot,x,y in FORMATION_SLOTS[self.formation]))
        if len(self.slots) != 11:
            raise ValueError('Tactical board requires 11 players.')
        if any(s.instruction not in VALID_INSTRUCTIONS for s in self.slots):
            raise ValueError('Unsupported individual instruction')

    def assign(self, index: int, role: str | None = None, instruction: str | None = None) -> 'TacticalBoard':
        if not 0 <= index < 11: raise IndexError('Board slot index out of range')
        s=list(self.slots); old=s[index]
        s[index]=PlayerSlot(old.slot,old.x,old.y,role or old.role,instruction or old.instruction)
        return TacticalBoard(self.formation, tuple(s))

    def reposition(self, index: int, x: int, y: int) -> 'TacticalBoard':
        if not 0 <= index < 11: raise IndexError('Board slot index out of range')
        if not (5 <= x <= 95 and 5 <= y <= 95): raise ValueError('Board coordinates must be 5..95')
        s=list(self.slots); old=s[index]; s[index]=PlayerSlot(old.slot,x,y,old.role,old.instruction)
        return TacticalBoard(self.formation, tuple(s))

def board_effect(board: TacticalBoard, style, pressing: int, width: int, defensive_line: int) -> dict[str,float]:
    central=sum(1 for s in board.slots if 35 <= s.x <= 65)
    wide=sum(1 for s in board.slots if s.x < 25 or s.x > 75)
    advanced=sum(1 for s in board.slots if s.y >= 65)
    stay_back=sum(s.instruction in {'STAY_BACK','HOLD_POSITION'} for s in board.slots)
    press=sum(s.instruction=='PRESS' for s in board.slots)
    invert=sum(s.instruction=='INVERT' for s in board.slots)
    overlap=sum(s.instruction=='OVERLAP' for s in board.slots)
    roam=sum(s.instruction=='ROAM' for s in board.slots)
    return {
        'central_control': 1 + (central-4)*.018 + (invert+roam)*.012,
        'width_attack': 1 + (wide-3)*.018 + (overlap)*.018 + (width-50)*.001,
        'chance_creation': 1 + (advanced-3)*.012 + (roam)*.012 + (pressing-50)*.0005,
        'transition_def': 1 + (stay_back)*.018 + (defensive_line-50)*.0007,
        'pressing': 1 + (press+pressing/25)*.008,
        'counter_exposure': 1 + max(0, advanced-3)*.018 + max(0, defensive_line-60)*.002,
    }


def phase_profile(board: TacticalBoard, style, pressing: int, width: int, defensive_line: int) -> dict[str,float]:
    """Translate the board into interpretable football phases.

    Values are multipliers around 1.0 and intentionally remain small so player
    quality, talents and tactical decisions all matter together.
    """
    central=sum(1 for s in board.slots if 35 <= s.x <= 65)
    advanced=sum(1 for s in board.slots if s.y >= 65)
    deep=sum(1 for s in board.slots if s.y <= 35)
    wide=sum(1 for s in board.slots if s.x < 25 or s.x > 75)
    inverted=sum(s.instruction=='INVERT' for s in board.slots)
    roam=sum(s.instruction=='ROAM' for s in board.slots)
    press=sum(s.instruction=='PRESS' for s in board.slots)
    stay=sum(s.instruction in {'STAY_BACK','HOLD_POSITION'} for s in board.slots)
    overlap=sum(s.instruction=='OVERLAP' for s in board.slots)
    return {
      'build_up':1+(central-4)*.012+inverted*.018+roam*.008+(50-pressing)*.0003,
      'possession':1+(central-4)*.014+roam*.008+(50-pressing)*.0004,
      'transition_attack':1+(advanced-3)*.012+overlap*.012,
      'transition_def':1+(deep-4)*.012+stay*.018+(50-defensive_line)*.0005,
      'pressing':1+press*.014+pressing*.0008,
      'defensive_block':1+stay*.016+(50-defensive_line)*.0007,
      'width':1+(wide-3)*.015+overlap*.012+(width-50)*.0008,
    }
