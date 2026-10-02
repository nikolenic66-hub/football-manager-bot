from dataclasses import dataclass
from app.models import Player

def clamp(v,lo=1,hi=100): return max(lo,min(hi,v))
WEIGHTS={
'GK':(.05,.10,.05,0,.20,.20,.15,.25),'CB':(.05,.02,.12,.04,.35,.20,.12,.10),
'LB':(.15,.02,.12,.08,.25,.15,.13,.10),'RB':(.15,.02,.12,.08,.25,.15,.13,.10),
'DM':(.06,.03,.20,.08,.25,.15,.13,.10),'CM':(.10,.06,.22,.15,.10,.12,.15,.10),
'AM':(.15,.16,.18,.20,.03,.08,.08,.12),'LW':(.20,.22,.10,.22,.02,.07,.08,.09),
'RW':(.20,.22,.10,.22,.02,.07,.08,.09),'ST':(.18,.30,.08,.17,.01,.12,.07,.07)}
def player_rating(p):
    attrs=(p.pace,p.shooting,p.passing,p.dribbling,p.defending,p.physical,p.stamina,p.mental)
    base=sum(a*w for a,w in zip(attrs,WEIGHTS[p.position]))
    morale=getattr(p,'morale',70)
    morale_factor=.94 + .06*clamp(morale,0,100)/100
    return round(clamp(base*(.88+.12*p.fitness/100)*(1+.012*p.form)*morale_factor),1)
@dataclass(frozen=True)
class TeamStrength:
    attack:float; midfield:float; defense:float; goalkeeper:float; overall:float
def avg(xs):
    xs=list(xs); return sum(xs)/len(xs) if xs else 50
def team_strength(players,formation):
    # A red card or untreated injury can leave a side with fewer than 11.
    # Keep the engine playable for 7-11 players instead of crashing live matches.
    if not 7 <= len(players) <= 11: raise ValueError('A match lineup must contain between 7 and 11 players.')
    atk=[p for p in players if p.position in {'ST','LW','RW','AM'}]
    mid=[p for p in players if p.position in {'DM','CM','AM'}]
    de=[p for p in players if p.position in {'CB','LB','RB','DM'}]
    gk=[p for p in players if p.position=='GK']
    a,m,d,g=avg(map(player_rating,atk)),avg(map(player_rating,mid)),avg(map(player_rating,de)),avg(map(player_rating,gk))
    return TeamStrength(a,m,d,g,.38*a+.27*m+.27*d+.08*g)
