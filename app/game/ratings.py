from dataclasses import dataclass
from app.models import Player
from app.game.positions import position_penalty

def clamp(v,lo=1,hi=100): return max(lo,min(hi,v))
WEIGHTS={
'GK':(.05,.10,.05,0,.20,.20,.15,.25),'CB':(.05,.02,.12,.04,.35,.20,.12,.10),
'LB':(.15,.02,.12,.08,.25,.15,.13,.10),'RB':(.15,.02,.12,.08,.25,.15,.13,.10),
'DM':(.06,.03,.20,.08,.25,.15,.13,.10),'CM':(.10,.06,.22,.15,.10,.12,.15,.10),
'AM':(.15,.16,.18,.20,.03,.08,.08,.12),'LW':(.20,.22,.10,.22,.02,.07,.08,.09),
'RW':(.20,.22,.10,.22,.02,.07,.08,.09),'ST':(.18,.30,.08,.17,.01,.12,.07,.07)}
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
    # Live incidents can reduce a team below seven players. The engine must not
    # crash; missing players reduce effective strength instead. An empty side has
    # zero strength and therefore cannot generate goals.
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
            if i < len(board.slots):
                slot_by_id[p.id]=board.slots[i].slot
    rated=lambda p: player_rating(p, slot_by_id.get(p.id))
    a,m,d,g=avg(map(rated,atk)),avg(map(rated,mid)),avg(map(rated,de)),avg(map(rated,gk))
    availability=.55 + .45*(len(players)/11)
    a*=availability; m*=availability; d*=availability; g*=availability
    return TeamStrength(a,m,d,g,.38*a+.27*m+.27*d+.08*g)
