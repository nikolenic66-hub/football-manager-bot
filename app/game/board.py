from dataclasses import dataclass, field

@dataclass(frozen=True)
class PlayerSlot:
    slot: str
    x: int
    y: int
    role: str = 'STARTER'
    instruction: str = 'BALANCED'

FORMATION_SLOTS = {
    '4-3-3': [('GK',10,50),('LB',25,15),('CB',25,38),('CB',25,62),('RB',25,85),('CM',48,30),('DM',45,50),('CM',48,70),('LW',72,15),('ST',82,50),('RW',72,85)],
    '4-4-2': [('GK',10,50),('LB',25,15),('CB',25,38),('CB',25,62),('RB',25,85),('LM',48,15),('CM',48,38),('CM',48,62),('RM',48,85),('ST',80,40),('ST',80,60)],
    '4-2-3-1': [('GK',10,50),('LB',25,15),('CB',25,38),('CB',25,62),('RB',25,85),('DM',45,40),('DM',45,60),('LW',67,18),('AM',67,50),('RW',67,82),('ST',82,50)],
    '3-5-2': [('GK',10,50),('CB',25,30),('CB',25,50),('CB',25,70),('LM',48,10),('CM',48,35),('DM',45,50),('CM',48,65),('RM',48,90),('ST',80,40),('ST',80,60)],
    '5-3-2': [('GK',10,50),('LB',25,10),('CB',25,30),('CB',25,50),('CB',25,70),('RB',25,90),('CM',50,32),('DM',48,50),('CM',50,68),('ST',80,40),('ST',80,60)],
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
    central=sum(1 for s in board.slots if 35 <= s.y <= 65)
    wide=sum(1 for s in board.slots if s.y < 25 or s.y > 75)
    advanced=sum(1 for s in board.slots if s.x >= 65)
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
    central=sum(1 for s in board.slots if 35 <= s.y <= 65)
    advanced=sum(1 for s in board.slots if s.x >= 65)
    deep=sum(1 for s in board.slots if s.x <= 35)
    wide=sum(1 for s in board.slots if s.y < 25 or s.y > 75)
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
