from dataclasses import dataclass
from enum import Enum


class Style(str, Enum):
    BALANCE='BALANCE'; ATTACK='ATTACK'; COUNTER='COUNTER'; DEFENSE='DEFENSE'; POSSESSION='POSSESSION'


ALLOWED_FORMATIONS = (
    '4-3-3', '4-4-2', '4-2-3-1', '3-5-2', '5-3-2',
    '4-1-2-1-2', '4-3-1-2', '4-5-1', '3-4-3', '4-1-4-1',
)

FORMATION_PROFILE = {
    '4-3-3': {'def':4,'mid':3,'att':3,'wide':2,'dm':1,'am':0, 'defenders':4,'midfielders':3,'attackers':3,'wingers':2},
    '4-4-2': {'def':4,'mid':4,'att':2,'wide':2,'dm':0,'am':0, 'defenders':4,'midfielders':4,'attackers':2,'wingers':2},
    '4-2-3-1': {'def':4,'mid':5,'att':1,'wide':2,'dm':2,'am':1, 'defenders':4,'midfielders':5,'attackers':1,'wingers':2},
    '3-5-2': {'def':3,'mid':5,'att':2,'wide':2,'dm':1,'am':0, 'defenders':3,'midfielders':5,'attackers':2,'wingers':0},
    '5-3-2': {'def':5,'mid':3,'att':2,'wide':2,'dm':1,'am':0, 'defenders':5,'midfielders':3,'attackers':2,'wingers':0},
    '4-1-2-1-2': {'def':4,'mid':4,'att':2,'wide':0,'dm':1,'am':1, 'defenders':4,'midfielders':4,'attackers':2,'wingers':0},
    '4-3-1-2': {'def':4,'mid':4,'att':2,'wide':0,'dm':0,'am':1, 'defenders':4,'midfielders':4,'attackers':2,'wingers':0},
    '4-5-1': {'def':4,'mid':5,'att':1,'wide':2,'dm':0,'am':0, 'defenders':4,'midfielders':5,'attackers':1,'wingers':2},
    '3-4-3': {'def':3,'mid':4,'att':3,'wide':2,'dm':0,'am':0, 'defenders':3,'midfielders':4,'attackers':3,'wingers':2},
    '4-1-4-1': {'def':4,'mid':5,'att':1,'wide':2,'dm':1,'am':0, 'defenders':4,'midfielders':5,'attackers':1,'wingers':2},
}

FORMATION_STRENGTHS = {
    '4-3-3': ('width', 'pressing', 'flank transitions'),
    '4-4-2': ('balanced structure', 'two-striker threat', 'wide coverage'),
    '4-2-3-1': ('central control', 'AM between lines', 'rest defence'),
    '3-5-2': ('central overload', 'wing-back width', 'two strikers'),
    '5-3-2': ('defensive density', 'central compactness', 'counter attacks'),
    '4-1-2-1-2': ('central overload', 'AM link play', 'two-striker combinations'),
    '4-3-1-2': ('central combinations', 'AM between lines', 'two-striker support'),
    '4-5-1': ('midfield control', 'defensive stability', 'compact block'),
    '3-4-3': ('front-three pressure', 'wide attack', 'high attacking numbers'),
    '4-1-4-1': ('two compact blocks', 'central protection', 'controlled transitions'),
}


FORMATION_MATCHUPS = {
    '4-3-3': {'favorable_against': ('5-3-2','4-1-2-1-2','4-3-1-2'), 'vulnerable_to': ('4-2-3-1','3-5-2')},
    '4-4-2': {'favorable_against': ('3-4-3','4-3-1-2'), 'vulnerable_to': ('4-2-3-1','4-1-4-1')},
    '4-2-3-1': {'favorable_against': ('4-4-2','4-3-1-2'), 'vulnerable_to': ('4-1-2-1-2','3-5-2')},
    '3-5-2': {'favorable_against': ('4-2-3-1','4-4-2'), 'vulnerable_to': ('4-3-3','3-4-3')},
    '5-3-2': {'favorable_against': ('4-3-1-2','4-1-2-1-2'), 'vulnerable_to': ('4-3-3','3-4-3')},
    '4-1-2-1-2': {'favorable_against': ('4-3-3','4-3-1-2'), 'vulnerable_to': ('3-4-3','4-5-1')},
    '4-3-1-2': {'favorable_against': ('4-4-2','5-3-2'), 'vulnerable_to': ('4-3-3','4-5-1')},
    '4-5-1': {'favorable_against': ('3-4-3','4-3-1-2'), 'vulnerable_to': ('4-3-3','4-2-3-1')},
    '3-4-3': {'favorable_against': ('5-3-2','4-5-1'), 'vulnerable_to': ('4-3-3','4-2-3-1')},
    '4-1-4-1': {'favorable_against': ('4-3-1-2','3-4-3'), 'vulnerable_to': ('4-3-3','4-2-3-1')},
}

FORMATION_WEAKNESSES = {
    '4-3-3': ('space behind fullbacks', 'central overloads'),
    '4-4-2': ('space between lines', 'central overloads'),
    '4-2-3-1': ('isolated striker', 'wide defensive shifts'),
    '3-5-2': ('space behind wing-backs', 'wide switches'),
    '5-3-2': ('limited width', 'lower attacking numbers'),
    '4-1-2-1-2': ('lack of width', 'exposed flanks'),
    '4-3-1-2': ('lack of width', 'flank defence'),
    '4-5-1': ('isolated striker', 'limited final-third numbers'),
    '3-4-3': ('space behind wide players', 'three-man defensive line'),
    '4-1-4-1': ('isolated striker', 'slow transition to attack'),
}


@dataclass(frozen=True)
class Tactics:
    formation:str='4-3-3'; style:Style=Style.BALANCE; tempo:int=50; pressing:int=50; width:int=50; defensive_line:int=50; aggression:int=50
    def __post_init__(self):
        if self.formation not in ALLOWED_FORMATIONS: raise ValueError('Unsupported formation')
        if any(not 0<=x<=100 for x in (self.tempo,self.pressing,self.width,self.defensive_line,self.aggression)): raise ValueError('Tactical sliders must be 0..100')

STYLE_SLIDERS={Style.BALANCE:(50,50,50,50,50),Style.ATTACK:(70,60,65,70,55),Style.DEFENSE:(40,45,40,30,45),Style.COUNTER:(65,30,45,25,50),Style.POSSESSION:(45,70,60,60,40)}

STYLE_MATCHUP = {
    Style.BALANCE: {Style.BALANCE:1.00, Style.ATTACK:.98, Style.DEFENSE:.95, Style.COUNTER:1.00, Style.POSSESSION:1.00},
    Style.ATTACK: {Style.BALANCE:1.03, Style.ATTACK:1.00, Style.DEFENSE:.95, Style.COUNTER:.97, Style.POSSESSION:1.04},
    Style.DEFENSE: {Style.BALANCE:.95, Style.ATTACK:1.00, Style.DEFENSE:1.00, Style.COUNTER:1.00, Style.POSSESSION:.98},
    Style.COUNTER: {Style.BALANCE:1.00, Style.ATTACK:1.00, Style.DEFENSE:.97, Style.COUNTER:1.00, Style.POSSESSION:1.05},
    Style.POSSESSION: {Style.BALANCE:1.00, Style.ATTACK:1.04, Style.DEFENSE:.98, Style.COUNTER:1.00, Style.POSSESSION:1.00},
}

def style_matchup_modifiers(own_style, opponent_style):
    """Return bounded attack/mid/defence effects for one side of a style matchup."""
    own_style=Style(own_style); opponent_style=Style(opponent_style)
    att=STYLE_MATCHUP[own_style][opponent_style]
    mid=1.0; defence=1.0
    pair={own_style,opponent_style}
    if own_style==Style.DEFENSE and opponent_style==Style.ATTACK:
        defence=1.05
    elif own_style==Style.ATTACK and opponent_style==Style.DEFENSE:
        att=.95
    elif pair=={Style.ATTACK,Style.POSSESSION}:
        att=1.04; defence=.97
    elif own_style==Style.DEFENSE and opponent_style==Style.COUNTER:
        defence=1.03
    elif own_style==Style.COUNTER and opponent_style==Style.DEFENSE:
        att=.97
    elif pair=={Style.DEFENSE,Style.POSSESSION}:
        att=.98
    elif pair=={Style.DEFENSE,Style.BALANCE}:
        att=.95; defence=1.05
    elif own_style==Style.COUNTER and opponent_style==Style.POSSESSION:
        att=1.05; mid=.95
    elif own_style==Style.POSSESSION and opponent_style==Style.COUNTER:
        mid=1.05
    elif own_style==Style.POSSESSION and opponent_style==Style.BALANCE:
        mid=1.05
    elif own_style==Style.ATTACK and opponent_style==Style.BALANCE:
        att=1.03
    return (max(.95,min(1.05,att)),max(.95,min(1.05,mid)),max(.95,min(1.05,defence)))

FORMATION_SLIDER_DELTAS = {
    '4-3-3': (5, 0, 10, 0, 0),
    '4-4-2': (0, 5, 0, 5, 0),
    '4-2-3-1': (10, 5, -5, 0, 0),
    '3-5-2': (0, 0, 10, 5, 5),
    '5-3-2': (-5, -10, 0, -15, 0),
    '4-1-2-1-2': (5, 5, -10, 0, 0),
    '4-3-1-2': (10, 0, -10, 5, 0),
    '4-5-1': (-10, -5, 5, -5, 0),
    '3-4-3': (10, 0, 15, 5, 10),
    '4-1-4-1': (0, -5, 0, -5, 0),
}

def apply_formation_sliders(style, formation):
    """Return style baseline plus formation deltas, clamped to 0..100."""
    style = Style(style)
    if formation not in FORMATION_SLIDER_DELTAS:
        raise ValueError('Unsupported formation')
    base = STYLE_SLIDERS[style]
    delta = FORMATION_SLIDER_DELTAS[formation]
    return tuple(max(0, min(100, base[i] + delta[i])) for i in range(5))
MODS={Style.BALANCE:(1,1),Style.ATTACK:(1.08,.94),Style.COUNTER:(1.05,.98),Style.DEFENSE:(.91,1.09),Style.POSSESSION:(1.02,1.03)}
def tactical_modifiers(t):
    a,d=MODS[t.style]; a*=.94+.12*t.tempo/100; a*=.96+.08*t.width/100; a*=.97+.06*t.pressing/100
    d*=.96+.08*t.defensive_line/100; d*=1.02-.06*t.aggression/100
    return a,d
