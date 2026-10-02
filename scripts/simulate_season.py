import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.models import Player
from app.game.engine import simulate_match
from app.game.league import round_robin
from app.game.tactics import Tactics

def team(start,b):
    ps=['GK','LB','CB','CB','RB','DM','CM','AM','LW','RW','ST']
    return [Player(start+i,'P',str(start+i),pos,25,b,b,b,b,b,b,b,b) for i,pos in enumerate(ps)]
teams={i:team(i*20,68+i*2) for i in range(8)}
table={i:[0,0,0,0,0,0,0] for i in teams}
for f in round_robin(list(teams),True):
    r=simulate_match(str(f.home_club_id),str(f.away_club_id),teams[f.home_club_id],teams[f.away_club_id],Tactics(),Tactics(),f.round_no*10000+f.home_club_id*101+f.away_club_id)
    h,a=table[f.home_club_id],table[f.away_club_id];h[0]+=1;a[0]+=1;h[4]+=r.home_score;h[5]+=r.away_score;a[4]+=r.away_score;a[5]+=r.home_score
    if r.home_score>r.away_score:h[1]+=1;h[6]+=3;a[3]+=1
    elif r.home_score<r.away_score:a[1]+=1;a[6]+=3;h[3]+=1
    else:h[2]+=1;a[2]+=1;h[6]+=1;a[6]+=1
for i,t in sorted(table.items(),key=lambda x:(x[1][6],x[1][4]-x[1][5],x[1][4]),reverse=True):print(i,t)
