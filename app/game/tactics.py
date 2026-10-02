from dataclasses import dataclass
from enum import Enum
class Style(str,Enum): BALANCE='BALANCE'; ATTACK='ATTACK'; COUNTER='COUNTER'; DEFENSE='DEFENSE'; POSSESSION='POSSESSION'
@dataclass(frozen=True)
class Tactics:
    formation:str='4-3-3'; style:Style=Style.BALANCE; tempo:int=50; pressing:int=50; width:int=50; defensive_line:int=50; aggression:int=50
    def __post_init__(self):
        if self.formation not in {'4-3-3','4-4-2','4-2-3-1','3-5-2','5-3-2'}: raise ValueError('Unsupported formation')
        if any(not 0<=x<=100 for x in (self.tempo,self.pressing,self.width,self.defensive_line,self.aggression)): raise ValueError('Tactical sliders must be 0..100')
MODS={Style.BALANCE:(1,1),Style.ATTACK:(1.08,.94),Style.COUNTER:(1.05,.98),Style.DEFENSE:(.91,1.09),Style.POSSESSION:(1.02,1.03)}
def tactical_modifiers(t):
    a,d=MODS[t.style]; a*=.94+.12*t.tempo/100; a*=.96+.08*t.width/100; a*=.97+.06*t.pressing/100
    d*=.96+.08*t.defensive_line/100; d*=1.02-.06*t.aggression/100
    return a,d
