"""Build deterministic visual assets for the game.

This creates formation boards, generic club crests, event icons and a fallback
card for every seeded player ID. Real portraits can replace the silhouette on
individual cards when portrait files are available.
"""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from random import Random
from PIL import Image, ImageDraw
import json
from app.visual.render import formation_board, club_crest, player_card
from app.player_catalog import REAL_ORDER, LEGEND_NAMES
from app.game.board import FORMATION_SLOTS

ROOT=Path(__file__).resolve().parents[1]; ICON=ROOT/'assets/visual/icons'; ICON.mkdir(parents=True,exist_ok=True)
FIRST=['Lars','Marco','Jonas','Daniel','Milan','Alex','Noah','Sven','Rafael','Nico','Victor','Leo','Max','Tom','Elias','Felix','Oscar','Lucas','Mateo','Ivan','Adrian','Julian','Matias','Dario','Enzo','Leon','Theo','Samuel','David','Oskar']
LAST=['van Dijk','Silva','Jansen','Costa','Martin','Santos','Muller','Rossi','Bakker','Novak','Kovacs','Fischer','Berg','Moretti','de Jong','Keller','Meyer','Pereira','Lind','Romero','Petrov','Nielsen','Andersson','Garcia','Fernandez','Mendez','Serrano','Kovac','Popescu','Marin']
POSITIONS=['GK']*100+['CB']*190+['LB']*90+['RB']*90+['DM']*100+['CM']*210+['AM']*120+['LW']*80+['RW']*80+['ST']*188

def stats(rng,base,pos):
    v={k:max(35,min(99,base+rng.randint(-9,9))) for k in ['pace','shooting','passing','dribbling','defending','physical']}
    if pos=='GK': v.update(pace=base-8,shooting=base-25,passing=base-3,dribbling=base-15,defending=base+3,physical=base+2)
    elif pos in ('CB','LB','RB'): v.update(defending=base+6,physical=base+3)
    elif pos in ('DM','CM','AM'): v.update(passing=base+5)
    else: v.update(shooting=base+6,dribbling=base+5,pace=base+4)
    return {k:max(20,min(99,int(x))) for k,x in v.items()}

def rarity(i,full):
    if full in LEGEND_NAMES:return 'LEGENDARY'
    if i<=110:return 'EPIC'
    if i<=360:return 'RARE'
    return 'BASE'

def icon(name, glyph):
    im=Image.new('RGBA',(160,160),(7,16,30,255)); d=ImageDraw.Draw(im); d.ellipse((18,18,142,142),fill=(18,45,70,255),outline=(80,180,255,255),width=4); d.text((80,82),glyph,anchor='mm',fill=(245,245,245,255))
    im.save(ICON/f'{name}.png')

for f in ('4-3-3','4-4-2','4-2-3-1','3-5-2','5-3-2'): formation_board(f)
formation_manifest={}
for f,slots in FORMATION_SLOTS.items():
    counts={}
    for slot,_,_ in slots: counts[slot]=counts.get(slot,0)+1
    formation_manifest[f]={'slots':[{'position':slot,'depth':x,'lateral':y} for slot,x,y in slots],'counts':counts}
(ROOT/'assets'/'visual'/'formation_manifest.json').write_text(json.dumps(formation_manifest,ensure_ascii=False,indent=2),encoding='utf-8')
for i,name in enumerate(['FOOTBALL MANAGER','UNITED','CITY','ATHLETIC','ROYAL','SPORTING','DYNAMO','LEGENDS','TITANS','RANGERS','WANDERERS','PHOENIX'],1): club_crest(name,i)
for n,g in {'goal':'⚽','yellow':'!','red':'×','sub':'↔','injury':'+','transfer':'€','whistle':'▶','tactics':'◇','star':'★','cup':'♛'}.items(): icon(n,g)

rng=Random(20261002); count=0
manifest=[]
for i in range(1,1249):
    if i<=len(REAL_ORDER):
        first,last,nat,pos=REAL_ORDER[i-1]
        full=f'{first} {last}'.strip()
    else:
        pos=POSITIONS[(i-121)%len(POSITIONS)]; first=FIRST[(i*7)%len(FIRST)]; last=f'{LAST[(i*11)%len(LAST)]} {i:04d}'; nat='NLD'; full=f'{first} {last}'
    rar=rarity(i,full); base={'BASE':63,'RARE':76,'EPIC':87,'LEGENDARY':94}[rar]
    target=ROOT/'assets/visual/cards'/f'player_{i}.png'
    portrait_candidates=[p for p in (ROOT/'assets'/'portraits'/f'{i}_real.png', ROOT/'assets'/'portraits'/f'{i}_avatar.png') if p.exists()]
    d=dict(id=i,first_name=first,last_name=last,nationality=nat,position=pos,rarity=rar,talents=['TACTICAL'],**stats(rng,base,pos))
    if portrait_candidates: d['portrait_path']=portrait_candidates[0]
    if not target.exists():
        player_card(d, target)
    count+=1
    manifest.append({'id':i,'first_name':first,'last_name':last,'nationality':nat,'position':pos,'rarity':rar,'portrait_file':str(portrait_candidates[0].relative_to(ROOT)) if portrait_candidates else None})
(ROOT/'assets'/'visual'/'cards_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
print(f'Built {count} player cards, 5 formations, 12 crests and 10 event icons.')
