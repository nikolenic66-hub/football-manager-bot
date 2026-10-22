from dataclasses import dataclass
from types import SimpleNamespace
from app.models import Player
from app.game.positions import position_penalty

def clamp(v,lo=1,hi=100): return max(lo,min(hi,v))
WEIGHTS={
'GK':(.05,.10,.05,0,.20,.20,.15,.25),'CB':(.05,.02,.12,.04,.35,.20,.12,.10),
'LB':(.15,.02,.12,.08,.25,.15,.13,.10),'RB':(.15,.02,.12,.08,.25,.15,.13,.10),
'DM':(.06,.03,.20,.08,.25,.15,.13,.10),'CM':(.10,.06,.22,.15,.10,.12,.15,.10),
'AM':(.15,.16,.18,.20,.03,.08,.08,.12),'LW':(.20,.22,.10,.22,.02,.07,.08,.09),
'RW':(.20,.22,.10,.22,.02,.07,.08,.09),'ST':(.18,.30,.08,.17,.01,.12,.07,.07)}

# Maximum attribute loss at fatigue=100. The requested headline losses are kept
# exact for pace/shooting/passing/defending; position profiles distribute the
# remaining physical/stamina/mental impact to the attributes that matter most.
_FATIGUE_LOSS={
    'GK':{'pace':.12,'shooting':.05,'passing':.10,'defending':.12,'physical':.15,'stamina':.20,'mental':.08},
    'CB':{'pace':.30,'shooting':.08,'passing':.15,'defending':.20,'physical':.22,'stamina':.25,'mental':.10},
    'LB':{'pace':.30,'shooting':.10,'passing':.15,'defending':.20,'physical':.22,'stamina':.25,'mental':.10},
    'RB':{'pace':.30,'shooting':.10,'passing':.15,'defending':.20,'physical':.22,'stamina':.25,'mental':.10},
    'DM':{'pace':.28,'shooting':.10,'passing':.15,'defending':.20,'physical':.22,'stamina':.25,'mental':.10},
    'CM':{'pace':.28,'shooting':.12,'passing':.15,'defending':.18,'physical':.22,'stamina':.25,'mental':.10},
    'AM':{'pace':.30,'shooting':.20,'passing':.15,'defending':.12,'physical':.20,'stamina':.25,'mental':.10},
    'LW':{'pace':.30,'shooting':.20,'passing':.15,'defending':.10,'physical':.22,'stamina':.25,'mental':.10},
    'RW':{'pace':.30,'shooting':.20,'passing':.15,'defending':.10,'physical':.22,'stamina':.25,'mental':.10},
    'ST':{'pace':.30,'shooting':.20,'passing':.15,'defending':.08,'physical':.22,'stamina':.25,'mental':.10},
}


def effective_attributes(player, fatigue):
    """Return fatigue-adjusted football attributes without mutating the player."""
    fatigue=max(0.0,min(100.0,float(fatigue or 0.0))) / 100.0
    profile=_FATIGUE_LOSS.get(getattr(player,'position','CM'),_FATIGUE_LOSS['CM'])
    attrs={}
    for name in ('pace','shooting','passing','defending','physical','stamina','mental'):
        value=float(getattr(player,name,0) or 0)
        attrs[name]=round(max(1.0,value*(1.0-profile.get(name,0.0)*fatigue)),2)
    return attrs


def fatigue_adjusted_player(player, fatigue):
    attrs=effective_attributes(player,fatigue)
    try:
        adjusted=SimpleNamespace(**vars(player))
    except TypeError:
        adjusted=SimpleNamespace()
    for name in ('id','first_name','last_name','position','age','dribbling','fitness','form','suspended','talents','morale'):
        if hasattr(player,name): setattr(adjusted,name,getattr(player,name))
    if not hasattr(adjusted,'dribbling'): adjusted.dribbling=float(getattr(player,'dribbling',0) or 0)
    if not hasattr(adjusted,'fitness'): adjusted.fitness=int(getattr(player,'fitness',100) or 100)
    if not hasattr(adjusted,'form'): adjusted.form=int(getattr(player,'form',0) or 0)
    if not hasattr(adjusted,'morale'): adjusted.morale=int(getattr(player,'morale',70) or 70)
    for name,value in attrs.items():
        setattr(adjusted,name,value)
    return adjusted


def player_rating(p, slot_position=None):
    attrs=(p.pace,p.shooting,p.passing,p.dribbling,p.defending,p.physical,p.stamina,p.mental)
    base=sum(a*w for a,w in zip(attrs,WEIGHTS[p.position]))
    morale=getattr(p,'morale',70)
    morale_factor=.94 + .06*clamp(morale,0,100)/100
    rating=clamp(base*(.88+.12*p.fitness/100)*(1+.012*p.form)*morale_factor)
    if slot_position is not None:
        rating *= 1 - position_penalty(p.position, slot_position)
    return round(clamp(rating),1)

@dataclass(frozen=True)
class TeamStrength:
    attack:float; midfield:float; defense:float; goalkeeper:float; overall:float

def avg(xs):
    xs=list(xs); return sum(xs)/len(xs) if xs else 50

def team_strength(players,formation,board=None):
    if len(players)>11: raise ValueError('A match lineup cannot contain more than 11 players.')
    if not players:
        return TeamStrength(0,0,0,0,0)
    atk=[p for p in players if p.position in {'ST','LW','RW','AM'}]
    mid=[p for p in players if p.position in {'DM','CM','AM'}]
    de=[p for p in players if p.position in {'CB','LB','RB','DM'}]
    gk=[p for p in players if p.position=='GK']
    slot_by_id={}
    if board is not None:
        for i,p in enumerate(players):
            if i < len(board.slots): slot_by_id[p.id]=board.slots[i].slot
    rated=lambda p: player_rating(p, slot_by_id.get(p.id))
    a,m,d,g=avg(map(rated,atk)),avg(map(rated,mid)),avg(map(rated,de)),avg(map(rated,gk))
    availability=.55 + .45*(len(players)/11)
    a*=availability; m*=availability; d*=availability; g*=availability
    return TeamStrength(a,m,d,g,.38*a+.27*m+.27*d+.08*g)
