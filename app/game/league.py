from dataclasses import dataclass
@dataclass(frozen=True)
class Fixture: round_no:int; home_club_id:int; away_club_id:int
def round_robin(club_ids,double_round=True):
    if not 4<=len(club_ids)<=12: raise ValueError('League must contain 4..12 clubs.')
    if len(club_ids)%2: raise ValueError('Number of clubs must be even.')
    arr=club_ids[:]; rounds=[]; n=len(arr)
    for r in range(n-1):
        pairs=[]
        for i in range(n//2):
            a,b=arr[i],arr[n-1-i]
            if (r+i)%2: a,b=b,a
            pairs.append((a,b))
        rounds.append(pairs); arr=[arr[0]]+[arr[-1]]+arr[1:-1]
    fs=[]
    for no,pairs in enumerate(rounds,1):
        fs += [Fixture(no,h,a) for h,a in pairs]
    if double_round:
        offset=n-1
        fs += [Fixture(offset+f.round_no,f.away_club_id,f.home_club_id) for f in fs[:len(club_ids)*(len(club_ids)-1)//2]]
    return fs
