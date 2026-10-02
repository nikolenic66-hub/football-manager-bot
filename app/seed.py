import asyncio
import json
from random import Random
from sqlalchemy import text
from .db import SessionLocal
from .player_catalog import REAL_ORDER, LEGEND_NAMES

TOTAL_PLAYERS=1248
RARITY_COUNTS={'BASE':888,'RARE':250,'EPIC':80,'LEGENDARY':30}
POSITIONS=['GK']*100+['CB']*190+['LB']*90+['RB']*90+['DM']*100+['CM']*210+['AM']*120+['LW']*80+['RW']*80+['ST']*188
FIRST=['Lars','Marco','Jonas','Daniel','Milan','Alex','Noah','Sven','Rafael','Nico','Victor','Leo','Max','Tom','Elias','Felix','Oscar','Lucas','Mateo','Ivan','Adrian','Julian','Matias','Dario','Enzo','Leon','Theo','Samuel','David','Oskar']
LAST=['van Dijk','Silva','Jansen','Costa','Martin','Santos','Muller','Rossi','Bakker','Novak','Kovacs','Fischer','Berg','Moretti','de Jong','Keller','Meyer','Pereira','Lind','Romero','Petrov','Nielsen','Andersson','Garcia','Fernandez','Mendez','Serrano','Kovac','Popescu','Marin']

def _stats(rng,base,pos):
    v={k:max(35,min(99,base+rng.randint(-9,9))) for k in ['pace','shooting','passing','dribbling','defending','physical','stamina','mental']}
    if pos=='GK':
        v.update(pace=max(35,min(99,base-8)),shooting=max(25,min(70,base-25)),passing=max(35,min(99,base-3)),dribbling=max(25,min(80,base-15)),defending=max(45,min(99,base+3)),physical=max(40,min(99,base+2)),stamina=max(40,min(99,base-2)),mental=max(40,min(99,base+4)))
    elif pos in ('CB','LB','RB'): v.update(defending=max(45,min(99,base+6)),physical=max(40,min(99,base+3)))
    elif pos in ('DM','CM','AM'): v.update(passing=max(45,min(99,base+5)),mental=max(40,min(99,base+3)))
    else: v.update(shooting=max(45,min(99,base+6)),dribbling=max(45,min(99,base+5)),pace=max(40,min(99,base+4)))
    return v

def _base_for(rng,rarity):
    return {'BASE':rng.randint(55,72),'RARE':rng.randint(70,82),'EPIC':rng.randint(82,91),'LEGENDARY':rng.randint(90,96)}[rarity]

TALENTS={
    'GK':['SWEEPER_KEEPER','SHOT_STOPPER','AERIAL_COMMANDER','DISTRIBUTOR'],
    'CB':['BALL_PLAYING_DEFENDER','STOPPER','AERIAL_BEAST','RECOVERY_DEFENDER','LEADER'],
    'LB':['OVERLAP_RUNNER','INVERTED_FULLBACK','CROSSER','ONE_V_ONE_DEFENDER'],
    'RB':['OVERLAP_RUNNER','INVERTED_FULLBACK','CROSSER','ONE_V_ONE_DEFENDER'],
    'DM':['ANCHOR','BALL_WINNER','DEEP_PLAYMAKER','PRESS_RESISTANT','DESTROYER'],
    'CM':['BOX_TO_BOX','DEEP_PLAYMAKER','CARRIER','PRESS_RESISTANT','TEMPO_CONTROLLER'],
    'AM':['PLAYMAKER','THROUGH_BALLER','SECOND_STRIKER','PRESSING_10','CREATIVE_ENGINE'],
    'LW':['INSIDE_FORWARD','WINGER','CREATIVE_DRIBBLER','COUNTER_RUNNER','PRESSER'],
    'RW':['INSIDE_FORWARD','WINGER','CREATIVE_DRIBBLER','COUNTER_RUNNER','PRESSER'],
    'ST':['POACHER','TARGET_MAN','PRESSING_FORWARD','FALSE_NINE','COMPLETE_FORWARD'],
}

def _talents(rng,pos,rarity):
    pool=TALENTS[pos]; count=1 if rarity=='BASE' else (2 if rarity=='RARE' else 3)
    return rng.sample(pool,k=min(count,len(pool)))

def _insert_values(i,first,last,nat,pos,rarity,rng,real=False,card='STANDARD',market=None,age=None):
    base=_base_for(rng,rarity); st=_stats(rng,base,pos)
    if market is None: market=int((base/60)**6*180_000)
    if rarity=='LEGENDARY': market=max(market,25_000_000)
    elif rarity=='EPIC': market=max(market,8_000_000)
    elif rarity=='RARE': market=max(market,2_000_000)
    talents=_talents(rng,pos,rarity)
    return dict(id=i,first_name=first,last_name=last,nationality=nat[:3].upper(),age=age or rng.randint(18,34),position=pos,**st,market_value=market,salary=max(15_000,market//35),potential=min(99,base+rng.randint(0,7)),rarity=rarity,card_version=card,real_player=real,preferred_foot='L' if rng.random()<.28 else 'R',secondary_positions=[],portrait_url=None,portrait_source=None,talents=json.dumps(talents),tactical_archetype=talents[0],data_source='curated_real' if real else 'generated')


async def seed():
    rng=Random(20261002)
    async with SessionLocal() as s:
        count=(await s.execute(text('SELECT count(*) FROM players'))).scalar_one()
        if count: return count
        rows=[]
        used=set()
        # Premium real players.
        for i,(first,last,nat,pos) in enumerate(REAL_ORDER,1):
            full=f'{first} {last}'.strip()
            rarity='LEGENDARY' if full in LEGEND_NAMES else ('EPIC' if i<=110 else 'RARE')
            rows.append(_insert_values(i,first,last,nat,pos,rarity,rng,real=True,card='ICON' if rarity=='LEGENDARY' else ('EPIC' if rarity=='EPIC' else 'STANDARD')))
            used.add(full.lower())
        # Complete the market to 1,248 cards. Generated cards are intentionally separated from real cards.
        for i in range(len(rows)+1,TOTAL_PLAYERS+1):
            rarity='RARE' if i<=360 else 'BASE'
            # The first 120 are real premium cards; positions are balanced for squad construction.
            pos=POSITIONS[(i-121)%len(POSITIONS)]
            first=FIRST[(i*7)%len(FIRST)]; last=f'{LAST[(i*11)%len(LAST)]} {i:04d}'
            full=f'{first} {last}'
            rows.append(_insert_values(i,first,last,'NLD',pos,rarity,rng,real=False))
        for v in rows:
            await s.execute(text('''INSERT INTO players(id,first_name,last_name,nationality,age,position,pace,shooting,passing,dribbling,defending,physical,stamina,mental,market_value,salary,potential,rarity,card_version,real_player,preferred_foot,secondary_positions,portrait_url,portrait_source,talents,tactical_archetype,data_source)
            VALUES(:id,:first_name,:last_name,:nationality,:age,:position,:pace,:shooting,:passing,:dribbling,:defending,:physical,:stamina,:mental,:market_value,:salary,:potential,:rarity,:card_version,:real_player,:preferred_foot,:secondary_positions,:portrait_url,:portrait_source,:talents,:tactical_archetype,:data_source)'''),v)
        await s.commit()
    return len(rows)

async def main(): print(f'Seeded {await seed()} players.')
if __name__=='__main__': asyncio.run(main())
