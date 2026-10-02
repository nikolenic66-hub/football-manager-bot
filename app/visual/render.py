from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import hashlib, math
from app.game.economy import stadium_capacity

ROOT=Path(__file__).resolve().parents[2]
ASSET=ROOT/'assets'/'visual'
CARD_DIR=ASSET/'cards'; FORM_DIR=ASSET/'formations'; CLUB_DIR=ASSET/'clubs'
for p in (CARD_DIR,FORM_DIR,CLUB_DIR): p.mkdir(parents=True,exist_ok=True)
FONT='/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
FONT_B='/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
RARITY={'BASE':((45,55,68),(120,135,150)),'RARE':((15,72,42),(75,220,120)),'EPIC':((58,24,86),(205,95,255)),'LEGENDARY':((76,53,12),(255,215,80))}

def font(size,bold=False):
    return ImageFont.truetype(FONT_B if bold else FONT,size)

def rounded(draw,box,r=24,fill=None,outline=None,w=2): draw.rounded_rectangle(box,radius=r,fill=fill,outline=outline,width=w)

def text_center(draw,xy,s,f,fill):
    bb=draw.textbbox((0,0),s,font=f); draw.text((xy[0]-(bb[2]-bb[0])/2,xy[1]-(bb[3]-bb[1])/2),s,font=f,fill=fill)

def silhouette(draw,cx,cy,scale,accent):
    draw.ellipse((cx-42*scale,cy-120*scale,cx+42*scale,cy-36*scale),fill=(225,228,232),outline=accent,width=max(2,int(4*scale)))
    draw.rounded_rectangle((cx-92*scale,cy-25*scale,cx+92*scale,cy+135*scale),radius=int(45*scale),fill=(215,219,224),outline=accent,width=max(2,int(4*scale)))

def player_card(player,out=None,size=(620,860)):
    rarity=player.get('rarity','BASE'); bg,accent=RARITY.get(rarity,RARITY['BASE'])
    im=Image.new('RGB',size,(5,12,24)); d=ImageDraw.Draw(im)
    # Fast deterministic background: a solid rarity tone plus two depth bands.
    d.rectangle((0,0,size[0],size[1]),fill=bg)
    d.rectangle((0,0,size[0],120),fill=tuple(max(0,int(c*0.72)) for c in bg))
    d.rectangle((0,size[1]-90,size[0],size[1]),fill=(7,12,22))
    rounded(d,(18,18,size[0]-18,size[1]-18),32,fill=None,outline=accent,w=8)
    rounded(d,(42,42,size[0]-42,530),26,fill=(9,20,34),outline=accent,w=3)
    # Prefer an approved local portrait when available; otherwise keep the deterministic silhouette.
    portrait = player.get('portrait_path') or player.get('portrait_url')
    portrait_path = None
    if portrait:
        pp=Path(str(portrait))
        portrait_path = pp if pp.is_absolute() else ROOT/pp
    if portrait_path and portrait_path.exists():
        try:
            src=Image.open(portrait_path).convert('RGB')
            target_w,target_h=size[0]-92,455
            scale=max(target_w/src.width,target_h/src.height)
            nw,nh=max(1,int(src.width*scale)),max(1,int(src.height*scale))
            src=src.resize((nw,nh))
            left=(nw-target_w)//2; top=(nh-target_h)//2
            src=src.crop((left,top,left+target_w,top+target_h))
            mask=Image.new('L',(target_w,target_h),0); md=ImageDraw.Draw(mask); md.rounded_rectangle((0,0,target_w-1,target_h-1),radius=24,fill=255)
            im.paste(src,(46,46),mask)
            d=ImageDraw.Draw(im)
        except Exception:
            silhouette(d,size[0]//2,320,1.65,accent)
    else:
        silhouette(d,size[0]//2,320,1.65,accent)
    name=f"{player.get('first_name','')} {player.get('last_name','')}".strip()
    text_center(d,(90,95),str(player.get('overall','—')),font(68,True),(245,245,245))
    text_center(d,(90,157),player.get('position',''),font(30,True),accent)
    text_center(d,(size[0]//2,570),name[:25],font(32,True),(250,250,250))
    text_center(d,(size[0]//2,610),f"{player.get('nationality','---')}  •  {rarity}",font(21),accent)
    stats=[('PAC','pace'),('SHO','shooting'),('PAS','passing'),('DRI','dribbling'),('DEF','defending'),('PHY','physical')]
    for i,(label,key) in enumerate(stats):
        col=i%2; row=i//2; x=70+col*250; y=655+row*55
        d.text((x,y),label,font=font(19,True),fill=(180,190,205)); d.text((x+62,y),str(player.get(key,'—')),font=font(22,True),fill=(245,245,245))
        d.rounded_rectangle((x+118,y+5,x+225,y+20),radius=7,fill=(35,45,58)); val=int(player.get(key,0) or 0); d.rounded_rectangle((x+118,y+5,x+118+107*val/100,y+20),radius=7,fill=accent)
    talents=player.get('talents') or []
    if isinstance(talents,str): talents=[talents]
    d.text((55,815),(' • '.join(talents[:3]))[:45],font=font(18,True),fill=accent)
    if out is None: out=CARD_DIR/f"player_{player.get('id','x')}.png"
    im.save(out)
    return out

def formation_positions(formation):
    """Return image coordinates derived directly from the engine's formation slots.

    Engine coordinates are depth (x) + lateral (y); image coordinates are
    lateral (x) + vertical (y), with the own goal at the bottom. Keeping one
    source of truth prevents visual/engine formation drift.
    """
    from app.game.board import FORMATION_SLOTS
    if formation not in FORMATION_SLOTS:
        raise ValueError(f'Unsupported formation: {formation}')
    return [(slot_y, 100-slot_x) for _,slot_x,slot_y in FORMATION_SLOTS[formation]]


def formation_board(formation='4-3-3', labels=None, out=None, size=(1000,680)):
    # The slot map is the source of truth for both the picture and the engine.
    # This prevents a visual 3-5-2 from accidentally looking like another shape.
    from app.game.board import FORMATION_SLOTS
    if formation not in FORMATION_SLOTS:
        raise ValueError(f'Unsupported formation: {formation}')
    im=Image.new('RGB',size,(6,30,20)); d=ImageDraw.Draw(im)
    d.rectangle((30,30,size[0]-30,size[1]-30),fill=(16,105,58),outline=(220,240,220),width=4)
    x0,y0,x1,y1=30,30,size[0]-30,size[1]-30
    d.line((x0,(y0+y1)//2,x1,(y0+y1)//2),fill=(220,240,220),width=3); d.ellipse(((size[0]//2)-80,(size[1]//2)-80,(size[0]//2)+80,(size[1]//2)+80),outline=(220,240,220),width=3)
    d.rectangle((x0+230,y1-120,x1-230,y1),outline=(220,240,220),width=3); d.rectangle((x0+230,y0,x1-230,y0+120),outline=(220,240,220),width=3)
    pos=formation_positions(formation)
    for i,(px,py) in enumerate(pos):
        x=x0+(x1-x0)*px/100; y=y0+(y1-y0)*py/100
        d.ellipse((x-22,y-22,x+22,y+22),fill=(30,130,255),outline=(255,255,255),width=3)
        text_center(d,(x,y),str(i+1),font(18,True),(255,255,255))
        slot_name=FORMATION_SLOTS[formation][i][0]
        label=(labels[i] if labels and i<len(labels) else slot_name)
        text_center(d,(x,y+38),str(label)[:12],font(17,True),(255,255,255))
    counts={}
    for slot,_,_ in FORMATION_SLOTS[formation]: counts[slot]=counts.get(slot,0)+1
    count_text=' · '.join(f'{k}×{v}' for k,v in counts.items())
    text_center(d,(size[0]//2,size[1]-12),count_text,font(16,True),(210,235,225))
    text_center(d,(size[0]//2,15),formation,font(26,True),(255,255,255))
    if out is None: out=FORM_DIR/f"{formation.replace('-','_')}.png"
    im.save(out,optimize=True); return out

def club_crest(name,club_id=0,out=None,size=(420,420)):
    h=hashlib.sha256(f'{club_id}:{name}'.encode()).hexdigest(); c1=tuple(int(h[i:i+2],16) for i in (0,2,4)); c2=tuple(int(h[i:i+2],16) for i in (6,8,10))
    im=Image.new('RGBA',size,(0,0,0,0)); d=ImageDraw.Draw(im)
    cx=cy=size[0]//2
    pts=[(cx,25),(size[0]-65,65),(size[0]-85,275),(cx,385),(85,275),(65,65)]
    d.polygon(pts,fill=c1+(255,),outline=(240,240,240,255)); d.polygon([(cx,50),(size[0]-90,82),(size[0]-105,255),(cx,350),(105,255),(90,82)],fill=c2+(255,))
    text_center(d,(cx,200),'⚽',font(100,True),(255,255,255,255)); text_center(d,(cx,315),name[:12].upper(),font(24,True),(255,255,255,255))
    if out is None: out=CLUB_DIR/f"club_{club_id}.png"
    im.save(out); return out

def match_center(home,away,hs,ascore,home_xg=0,away_xg=0,events=None,out=None,size=(1200,720)):
    im=Image.new('RGB',size,(5,13,27)); d=ImageDraw.Draw(im)
    rounded(d,(20,20,size[0]-20,size[1]-20),28,fill=(8,22,42),outline=(34,130,210),w=3)
    text_center(d,(size[0]//2,75),'MATCH CENTER',font(30,True),(150,190,230))
    text_center(d,(300,155),home[:18],font(30,True),(245,245,245)); text_center(d,(900,155),away[:18],font(30,True),(245,245,245))
    text_center(d,(600,155),f'{hs} : {ascore}',font(58,True),(255,215,80))
    text_center(d,(600,225),f'xG  {home_xg:.2f}  —  {away_xg:.2f}',font(23,True),(170,190,210))
    d.rounded_rectangle((120,275,1080,315),radius=18,fill=(28,42,60)); total=max(0.01,home_xg+away_xg); split=960*home_xg/total; d.rounded_rectangle((120,275,120+split,315),radius=18,fill=(40,150,255));
    text_center(d,(600,300),'EXPECTED GOALS',font(18,True),(255,255,255))
    text_center(d,(600,365),'EVENTS',font(22,True),(150,190,230))
    y=410
    for e in (events or [])[-7:]:
        txt=f"{e.get('minute','')}′  {e.get('description','')}"
        d.text((100,y),txt[:95],font=font(21),fill=(235,240,245)); y+=38
    if out is None: out=ASSET/f"match_center_{hashlib.md5(f'{home}{away}{hs}{ascore}'.encode()).hexdigest()[:10]}.png"
    im.save(out,optimize=True); return out


def tactical_board_screen(formation='4-3-3', players=None, title='TACTICAL BOARD', out=None, size=(1400,820)):
    """Render the live tactical board with player labels, roles and instructions."""
    players = players or []
    im=Image.new('RGB',size,(4,10,22)); d=ImageDraw.Draw(im)
    rounded(d,(18,18,size[0]-18,size[1]-18),30,fill=(8,21,39),outline=(36,133,215),w=3)
    # pitch
    px0,py0,px1,py1=35,70,930,760
    d.rounded_rectangle((px0,py0,px1,py1),radius=18,fill=(18,112,62),outline=(210,240,220),width=4)
    d.line((px0,(py0+py1)//2,px1,(py0+py1)//2),fill=(225,245,230),width=3)
    cx=(px0+px1)//2; cy=(py0+py1)//2
    d.ellipse((cx-95,cy-95,cx+95,cy+95),outline=(225,245,230),width=3)
    d.line((cx,cy-95,cx,cy+95),fill=(225,245,230),width=3)
    d.rectangle((px0+260,py0,px1-260,py0+145),outline=(225,245,230),width=3)
    d.rectangle((px0+260,py1-145,px1-260,py1),outline=(225,245,230),width=3)
    d.text((38,25),title,font=font(28,True),fill=(235,242,250))
    d.text((700,32),formation,font=font(24,True),fill=(75,220,170))
    # right panel
    rounded(d,(960,70,1365,760),24,fill=(10,29,50),outline=(45,90,130),w=2)
    d.text((990,95),'STARTING XI',font=font(24,True),fill=(160,195,225))
    rows=players[:11]
    for i,p in enumerate(rows):
        y=145+i*49
        accent=(70,220,165) if i==0 else (80,150,220)
        d.ellipse((985,y,1019,y+34),fill=accent)
        text_center(d,(1002,y+17),str(i+1),font(16,True),(255,255,255))
        nm=f"{p.get('first_name','')} {p.get('last_name','')}".strip()[:18]
        d.text((1032,y+2),nm,font=font(17,True),fill=(242,246,250))
        meta=f"{p.get('position','')}  {p.get('role','STARTER')}"
        d.text((1032,y+24),meta[:28],font=font(13),fill=(145,170,195))
    # player markers use supplied board coordinates; fall back to formation map
    fallback=formation_positions(formation)
    for i,p in enumerate(rows):
        bx=p.get('board_x'); by=p.get('board_y')
        if bx is None or by is None: bx,by=fallback[i]
        x=px0+(px1-px0)*float(bx)/100; y=py0+(py1-py0)*float(by)/100
        radius=28
        d.ellipse((x-radius,y-radius,x+radius,y+radius),fill=(28,125,235),outline=(255,255,255),width=3)
        text_center(d,(x,y),str(i+1),font(17,True),(255,255,255))
        nm=f"{p.get('first_name','')} {p.get('last_name','')}".strip().split()
        short=(nm[-1] if nm else str(i+1))[:11]
        text_center(d,(x,y+43),short,font(14,True),(255,255,255))
        instr=str(p.get('instruction','BALANCED'))
        if instr!='BALANCED':
            text_center(d,(x,y-42),instr[:11],font(12,True),(255,235,130))
            # Visualize the same individual instruction used by the engine.
            if instr in {'OVERLAP','GET_FORWARD','ROAM'}:
                ex=x+55 if bx < 50 else x-55
                ey=y-45 if by < 50 else y+45
                d.line((x,y,ex,ey),fill=(90,235,180),width=4)
                d.ellipse((ex-5,ey-5,ex+5,ey+5),fill=(90,235,180))
            elif instr in {'STAY_BACK','HOLD_POSITION','MARK'}:
                d.line((x,y,x,y+34),fill=(255,205,95),width=3)
            elif instr=='INVERT':
                d.line((x,y,x,cy),fill=(110,185,255),width=3)
    if out is None: out=ASSET/f"tactical_board_{formation.replace('-','_')}.png"
    im.save(out,optimize=True); return out


def squad_screen(club_name, players, out=None, size=(1400,900)):
    im=Image.new('RGB',size,(4,10,22)); d=ImageDraw.Draw(im)
    rounded(d,(18,18,size[0]-18,size[1]-18),30,fill=(8,21,39),outline=(36,133,215),w=3)
    d.text((45,38),f'{club_name} — SQUAD',font=font(30,True),fill=(240,245,250))
    d.text((1100,44),f'{len(players)} PLAYERS',font=font(21,True),fill=(95,210,175))
    cols=2; rows_per=12
    for idx,p in enumerate(players[:24]):
        col=idx//rows_per; row=idx%rows_per
        x=45+col*665; y=100+row*62
        rarity=p.get('rarity','BASE'); _,accent=RARITY.get(rarity,RARITY['BASE'])
        d.ellipse((x,y,x+44,y+44),fill=(15,32,52),outline=accent,width=3)
        text_center(d,(x+22,y+22),str(p.get('shirt_number') or idx+1),font(15,True),(255,255,255))
        nm=f"{p.get('first_name','')} {p.get('last_name','')}".strip()[:23]
        d.text((x+58,y+1),nm,font=font(18,True),fill=(240,244,248))
        d.text((x+58,y+27),f"{p.get('position','')} · OVR {p.get('overall','—')} · FIT {p.get('fitness',100)} · FORM {p.get('form',0):+d}",font=font(13),fill=(150,175,200))
        if p.get('is_injured'): d.text((x+520,y+12),'🩹',font=font(20),fill=(255,100,100))
    if out is None: out=ASSET/'squad_screen.png'
    im.save(out,optimize=True); return out


def league_table_screen(league_name, rows, out=None, size=(1200,800)):
    im=Image.new('RGB',size,(4,10,22)); d=ImageDraw.Draw(im)
    rounded(d,(18,18,size[0]-18,size[1]-18),30,fill=(8,21,39),outline=(36,133,215),w=3)
    d.text((45,40),league_name,font=font(30,True),fill=(240,245,250))
    headers=['#','CLUB','P','W','D','L','GF','GA','PTS']
    xs=[50,105,620,720,800,880,960,1030,1110]
    for x,h in zip(xs,headers): d.text((x,100),h,font=font(17,True),fill=(115,170,210))
    for i,r in enumerate(rows[:12],1):
        y=145+(i-1)*50
        if i==1: rounded(d,(35,y-7,1165,y+35),12,fill=(44,48,26),outline=(175,145,50),w=1)
        d.text((xs[0],y),str(i),font=font(19,True),fill=(245,245,245))
        d.text((xs[1],y),str(r['name'])[:30],font=font(18,True),fill=(240,245,250))
        vals=[r['played'],r['wins'],r['draws'],r['losses'],r['goals_for'],r['goals_against'],r['points']]
        for x,v in zip(xs[2:],vals): d.text((x,y),str(v),font=font(18,True),fill=(205,220,235))
    if out is None: out=ASSET/'league_table.png'
    im.save(out,optimize=True); return out


def club_dashboard(club, out=None, size=(1200,760)):
    im=Image.new('RGB',size,(4,10,22)); d=ImageDraw.Draw(im)
    rounded(d,(18,18,size[0]-18,size[1]-18),30,fill=(8,21,39),outline=(36,133,215),w=3)
    crest_path=CLUB_DIR/f"club_{club.get('id',0)}.png"
    if not crest_path.exists(): club_crest(club.get('name','CLUB'),club.get('id',0),crest_path)
    try:
        cr=Image.open(crest_path).convert('RGBA').resize((250,250)); im.paste(cr,(70,95),cr)
    except Exception: pass
    d.text((355,80),str(club.get('name','CLUB'))[:24],font=font(38,True),fill=(245,248,252))
    d.text((355,135),str(club.get('stadium_name','Arena'))[:32],font=font(21),fill=(145,175,205))
    cards=[('BUDGET',f"€{int(club.get('budget',0)):,}"),("FANS",str(club.get('fans',0))),("REPUTATION",str(club.get('reputation',0))),("STADIUM",f"LVL {club.get('stadium_level',1)}")]
    for i,(label,val) in enumerate(cards):
        x=355+(i%2)*365; y=215+(i//2)*120
        rounded(d,(x,y,x+330,y+95),18,fill=(10,29,50),outline=(42,84,120),w=2)
        d.text((x+20,y+18),label,font=font(15,True),fill=(105,155,195)); d.text((x+20,y+47),val,font=font(27,True),fill=(240,245,250))
    d.text((70,510),'MANAGER HUB',font=font(22,True),fill=(115,190,225))
    items=['SQUAD','TACTICS','MATCH CENTER','MARKET','LEAGUE TABLE','FIXTURES']
    for i,item in enumerate(items):
        x=70+(i%3)*350; y=555+(i//3)*75
        rounded(d,(x,y,x+315,y+52),14,fill=(12,35,58),outline=(38,100,150),w=1); text_center(d,(x+157,y+26),item,font(16,True),(225,235,245))
    if out is None: out=ASSET/'club_dashboard.png'
    im.save(out,optimize=True); return out


def finance_screen(club, summary, recent, out=None, size=(1300,900)):
    im=Image.new('RGB',size,(4,10,22)); d=ImageDraw.Draw(im)
    rounded(d,(18,18,size[0]-18,size[1]-18),30,fill=(8,21,39),outline=(36,133,215),w=3)
    d.text((45,38),f"{club.get('name','CLUB')} · FINANCE",font=font(30,True),fill=(240,245,250))
    cards=[('BALANCE',f"€{int(club.get('budget',0)):,}"),('TICKET',f"€{int(club.get('ticket_price',20))}"),('SPONSOR',f"LVL {int(club.get('sponsor_level',1))}"),('CAPACITY',f"{stadium_capacity(int(club.get('stadium_level',1))):,}")]
    for i,(label,val) in enumerate(cards):
        x=45+i*300; rounded(d,(x,92,x+270,168),16,fill=(10,29,50),outline=(42,84,120),w=2)
        d.text((x+16,106),label,font=font(13,True),fill=(105,155,195)); d.text((x+16,133),val,font=font(23,True),fill=(240,245,250))
    labels={'TICKETS':'Билеты','SPONSOR':'Спонсор','WAGES':'Зарплаты','STADIUM':'Стадион','TRANSFER_IN':'Продажи','TRANSFER_OUT':'Покупки','PRIZE':'Призовые','OTHER':'Прочее'}
    d.text((45,205),'SEASON / LEDGER SUMMARY',font=font(20,True),fill=(115,190,225))
    for i,r in enumerate(summary[:8]):
        y=245+i*48; total=int(r.get('total',0)); sign='+' if total>=0 else ''
        rounded(d,(45,y,680,y+37),10,fill=(10,29,50),outline=(45,80,110),w=1)
        d.text((60,y+8),labels.get(r.get('category'),str(r.get('category'))),font=font(15,True),fill=(225,235,245))
        d.text((480,y+8),f'{sign}€{total:,}',font=font(15,True),fill=(110,205,160) if total>=0 else (235,135,125))
    d.text((730,205),'RECENT TRANSACTIONS',font=font(20,True),fill=(115,190,225))
    for i,r in enumerate(recent[:11]):
        y=245+i*52; amount=int(r.get('amount',0)); sign='+' if amount>=0 else ''
        rounded(d,(730,y,1255,y+42),10,fill=(10,29,50),outline=(45,80,110),w=1)
        desc=str(r.get('description',''))[:44]
        d.text((748,y+5),desc,font=font(13,True),fill=(225,235,245))
        d.text((1100,y+22),f'{sign}€{amount:,}',font=font(13,True),fill=(110,205,160) if amount>=0 else (235,135,125))
    if out is None: out=ASSET/f"finance_{club.get('id',0)}.png"
    im.save(out,optimize=True); return out

def market_screen(rows, out=None, size=(1300,850)):
    im=Image.new('RGB',size,(4,10,22)); d=ImageDraw.Draw(im)
    rounded(d,(18,18,size[0]-18,size[1]-18),30,fill=(8,21,39),outline=(36,133,215),w=3)
    d.text((45,38),'TRANSFER MARKET',font=font(30,True),fill=(240,245,250))
    for i,p in enumerate(rows[:10]):
        y=105+i*67; _,accent=RARITY.get(p.get('rarity','BASE'),RARITY['BASE'])
        rounded(d,(40,y,1260,y+54),14,fill=(10,29,50),outline=(45,80,110),w=1)
        d.text((58,y+12),f"#{p.get('id')}  {p.get('first_name','')} {p.get('last_name','')}"[:34],font=font(17,True),fill=(240,245,250))
        d.text((520,y+13),str(p.get('position','')),font=font(17,True),fill=accent)
        d.text((650,y+13),f"OVR {p.get('overall','—')}",font=font(16,True),fill=(180,205,225))
        d.text((820,y+13),str(p.get('rarity','BASE')),font=font(14,True),fill=accent)
        d.text((1010,y+13),f"€{int(p.get('market_value',0)):,}",font=font(17,True),fill=(235,240,245))
    if out is None: out=ASSET/'market_screen.png'
    im.save(out,optimize=True); return out


def fixtures_screen(league_name, rows, out=None, size=(1300,900)):
    im=Image.new('RGB',size,(4,10,22)); d=ImageDraw.Draw(im)
    rounded(d,(18,18,size[0]-18,size[1]-18),30,fill=(8,21,39),outline=(36,133,215),w=3)
    d.text((45,38),f'{league_name} · FIXTURES',font=font(30,True),fill=(240,245,250))
    for i,r in enumerate(rows[:14]):
        y=100+i*54; status=str(r.get('status',''))
        fill=(30,45,60) if status!='FINISHED' else (22,54,46)
        rounded(d,(40,y,1260,y+42),12,fill=fill,outline=(45,80,110),w=1)
        d.text((58,y+9),f"R{r.get('round')}" ,font=font(16,True),fill=(110,170,215))
        d.text((125,y+9),str(r.get('home',''))[:27],font=font(17,True),fill=(240,245,250))
        score=f"{r.get('home_score')} : {r.get('away_score')}" if status=='FINISHED' else '—'
        text_center(d,(650,y+21),score,font(17,True),(255,215,90) if status=='FINISHED' else (140,165,190))
        d.text((750,y+9),str(r.get('away',''))[:27],font=font(17,True),fill=(240,245,250))
        d.text((1130,y+9),status,font=font(13,True),fill=(110,185,155) if status=='FINISHED' else (130,155,180))
    if out is None: out=ASSET/'fixtures_screen.png'
    im.save(out,optimize=True); return out


def scouting_screen(report, out=None, size=(1400,900)):
    im=Image.new('RGB',size,(4,10,22)); d=ImageDraw.Draw(im)
    rounded(d,(18,18,size[0]-18,size[1]-18),30,fill=(8,21,39),outline=(36,133,215),w=3)
    d.text((45,38),f"SCOUTING · {report.opponent_name}",font=font(30,True),fill=(240,245,250))
    d.text((45,88),f"Последние матчи: {report.sample_matches} · Типовая схема: {report.typical_formation} · Стиль: {report.typical_style}",font=font(18,True),fill=(120,185,220))
    cards=[('СР. ГОЛЫ ЗА',f'{report.avg_goals_for:.2f}'),('СР. ГОЛЫ ПРОТИВ',f'{report.avg_goals_against:.2f}'),('ВЫБОРКА',str(report.sample_matches))]
    for i,(label,val) in enumerate(cards):
        x=45+i*430; rounded(d,(x,135,x+390,225),18,fill=(10,29,50),outline=(42,84,120),w=2)
        d.text((x+18,151),label,font=font(14,True),fill=(105,155,195)); d.text((x+18,178),val,font=font(27,True),fill=(240,245,250))
    d.text((45,260),'КЛЮЧЕВЫЕ ИГРОКИ',font=font(21,True),fill=(115,190,225))
    for i,name in enumerate(report.key_players):
        y=305+i*55; rounded(d,(45,y,620,y+42),12,fill=(10,29,50),outline=(45,80,110),w=1); d.text((65,y+10),f'{i+1}. {name}',font=font(17,True),fill=(240,245,250))
    d.text((700,260),'ТЕНДЕНЦИИ',font=font(21,True),fill=(115,190,225))
    for i,item in enumerate(report.tendencies):
        y=305+i*52; d.text((720,y),f'• {item}',font=font(17,True),fill=(225,235,245))
    d.text((700,500),'КОНТРТАКТИКА',font=font(21,True),fill=(255,215,90))
    for i,item in enumerate(report.counter_recommendations):
        y=545+i*58; rounded(d,(700,y,1345,y+45),12,fill=(34,37,24),outline=(145,125,50),w=1); d.text((720,y+11),f'• {item}'[:68],font=font(16,True),fill=(245,240,215))
    if out is None: out=ASSET/f"scouting_{report.opponent_id}.png"
    im.save(out,optimize=True); return out


def match_phase_screen(home, away, formation_home, formation_away, phase='POSSESSION', home_control=0.5,
                       pressure_home=50, pressure_away=50, events=None, out=None, size=(1400,900)):
    """Visual Match Center map derived from the same formation coordinates used by the engine."""
    im=Image.new('RGB',size,(4,10,22)); d=ImageDraw.Draw(im)
    rounded(d,(18,18,size[0]-18,size[1]-18),30,fill=(8,21,39),outline=(36,133,215),w=3)
    d.text((45,34),f'{home}  vs  {away}',font=font(29,True),fill=(240,245,250))
    d.text((45,78),f'PHASE · {phase}',font=font(19,True),fill=(90,215,175))
    # pitch
    px0,py0,px1,py1=45,125,1030,835
    d.rounded_rectangle((px0,py0,px1,py1),radius=18,fill=(18,112,62),outline=(215,240,225),width=4)
    for frac in (0.33,0.66):
        y=py0+(py1-py0)*frac; d.line((px0,y,px1,y),fill=(195,235,210),width=2)
    d.line(((px0+px1)//2,py0,(px0+px1)//2,py1),fill=(225,245,230),width=3)
    cx=(px0+px1)//2; cy=(py0+py1)//2
    d.ellipse((cx-80,cy-80,cx+80,cy+80),outline=(225,245,230),width=3)
    # home uses left-to-right engine depth; away is mirrored.
    for side,formation,base_x,label in ((home,formation_home,0,'HOME'),(away,formation_away,1,'AWAY')):
        for i,(ex,ey) in enumerate(formation_positions(formation)):
            sx,sy=ex,ey
            if base_x: sx=100-sx
            x=px0+(px1-px0)*sx/100; y=py0+(py1-py0)*sy/100
            fill=(40,140,245) if base_x==0 else (235,75,85)
            d.ellipse((x-16,y-16,x+16,y+16),fill=fill,outline=(255,255,255),width=2)
            text_center(d,(x,y),str(i+1),font(10,True),(255,255,255))
    # phase arrows: explicit, stable visual language rather than random decoration.
    arrows={
        'BUILD_UP':((px0+160,cy),(px0+420,cy-80)),
        'POSSESSION':((px0+390,cy),(px0+610,cy)),
        'PRESSING':((px0+610,cy),(px0+820,cy-100)),
        'TRANSITION':((px0+430,cy+100),(px0+820,cy-80)),
        'DEFENSIVE_BLOCK':((px0+760,cy),(px0+520,cy+60)),
        'COUNTER':((px0+430,cy+100),(px0+870,cy-120)),
    }
    if phase in arrows:
        (x1,y1),(x2,y2)=arrows[phase]
        d.line((x1,y1,x2,y2),fill=(255,220,90),width=8)
        ang=math.atan2(y2-y1,x2-x1); ah=18
        for a in (ang+2.6,ang-2.6): d.line((x2,y2,x2+ah*math.cos(a),y2+ah*math.sin(a)),fill=(255,220,90),width=5)
    # side panel
    rounded(d,(1060,125,1360,835),24,fill=(10,29,50),outline=(45,90,130),w=2)
    d.text((1090,155),'MATCH FLOW',font=font(23,True),fill=(160,195,225))
    labels=[('Контроль',f'{max(0,min(100,int(home_control*100)))}% / {max(0,min(100,100-int(home_control*100)))}%'),
            ('Прессинг',f'{pressure_home} / {pressure_away}'),('Фаза',phase),('Схема',f'{formation_home} / {formation_away}')]
    for i,(k,v) in enumerate(labels):
        y=215+i*92; d.text((1090,y),k,font=font(15,True),fill=(105,155,195)); d.text((1090,y+29),v,font=font(21,True),fill=(240,245,250))
    d.text((1090,590),'ПОСЛЕДНИЕ СОБЫТИЯ',font=font(16,True),fill=(115,190,225))
    y=625
    for e in (events or [])[-5:]:
        txt=f"{e.get('minute','')}′ {e.get('description','')}"[:28]
        d.text((1090,y),txt,font=font(13),fill=(225,235,245)); y+=34
    if out is None:
        out=ASSET/f"match_phase_{hashlib.md5(f'{home}{away}{phase}'.encode()).hexdigest()[:10]}.png"
    im.save(out,optimize=True); return out

def dynamic_match_screen(home, away, formation_home, formation_away, phase='POSSESSION', minute=45,
                         home_control=.5, pressure_home=50, pressure_away=50, events=None,
                         home_score=0, away_score=0, out=None, size=(1600,900)):
    """Render a deterministic tactical snapshot for the current match state.

    The player coordinates come from the same FORMATION_SLOTS used by the engine.
    Movement arrows/pressure zones are derived from phase and tactical intensity,
    so the image is an interpretation of engine state rather than a second layout system.
    """
    from app.game.board import FORMATION_SLOTS
    if formation_home not in FORMATION_SLOTS or formation_away not in FORMATION_SLOTS:
        raise ValueError('Unsupported formation')
    im=Image.new('RGB',size,(4,12,22)); d=ImageDraw.Draw(im)
    # Header
    d.text((45,28),f'{home}  {home_score}:{away_score}  {away}',font=font(30,True),fill=(245,248,252))
    d.text((45,70),f'{minute}′ · {phase}',font=font(18,True),fill=(110,200,235))
    # pitch
    px0,py0,px1,py1=45,125,1125,855
    d.rounded_rectangle((px0,py0,px1,py1),radius=24,fill=(10,82,52),outline=(115,205,150),width=3)
    for frac in (.2,.4,.6,.8):
        x=px0+(px1-px0)*frac; d.line((x,py0,x,py1),fill=(80,165,110),width=2)
    d.line(((px0+px1)//2,py0,(px0+px1)//2,py1),fill=(205,235,215),width=3)
    cx=(px0+px1)//2; cy=(py0+py1)//2; d.ellipse((cx-90,cy-90,cx+90,cy+90),outline=(205,235,215),width=3)
    # phase/pressure zones
    if phase in {'PRESSING','TRANSITION','COUNTER'}:
        alpha=max(25,min(90,25+abs(pressure_home-pressure_away)))
        # no alpha compositing dependency: use soft solid translucent-like bands
        if pressure_home>=pressure_away:
            d.rectangle((px0,py0,px0+300,py1),fill=(22,100,72))
        else:
            d.rectangle((px1-300,py0,px1,py1),fill=(100,42,52))
    def draw_team(formation,mirror,fill):
        pts=[]
        for slot,x,y in FORMATION_SLOTS[formation]:
            sx=100-x if mirror else x
            px=px0+(px1-px0)*sx/100; py=py0+(py1-py0)*y/100
            # small deterministic phase movement, tied to the same slot coordinates
            shift=0
            if phase in {'POSSESSION','BUILD_UP'} and slot in {'CM','DM','AM','LM','RM'}: shift=18
            elif phase in {'PRESSING'} and slot not in {'GK','CB'}: shift=14
            elif phase in {'COUNTER','TRANSITION'} and slot in {'ST','LW','RW','LM','RM'}: shift=24
            if mirror: px=max(px0+25,px-shift)
            else: px=min(px1-25,px+shift)
            pts.append((px,py,slot))
        for px,py,slot in pts:
            d.ellipse((px-19,py-19,px+19,py+19),fill=fill,outline=(255,255,255),width=2)
            text_center(d,(px,py),slot,font(10,True),(255,255,255))
        return pts
    hp=draw_team(formation_home,False,(35,145,245)); ap=draw_team(formation_away,True,(235,70,85))
    # Phase arrows and transition direction.
    if phase in {'COUNTER','TRANSITION'}:
        start=hp[8][:2] if len(hp)>8 else (px0+350,cy)
        end=(px1-120,cy-80)
        d.line((*start,*end),fill=(255,220,90),width=8)
    elif phase=='PRESSING':
        d.line((px1//2,cy,px1-120,cy),fill=(255,220,90),width=8)
    elif phase=='BUILD_UP':
        d.line((px0+120,cy,px0+430,cy-80),fill=(255,220,90),width=8)
    else:
        d.line((px0+430,cy,px1-430,cy),fill=(255,220,90),width=6)
    # If the engine supplied a concrete action, draw that action on top of the phase layer.
    visual_events=[e for e in (events or []) if isinstance(e,dict) and (e.get('metadata') or {}).get('start')]
    if visual_events:
        ev=visual_events[-1]; meta=ev.get('metadata') or {}; start=meta.get('start'); end=meta.get('end')
        if isinstance(start,(list,tuple)) and len(start)>=2:
            sx=px0+(px1-px0)*float(start[0])/100; sy=py0+(py1-py0)*float(start[1])/100
            ex=px0+(px1-px0)*float(end[0])/100 if isinstance(end,(list,tuple)) and len(end)>=2 else sx
            ey=py0+(py1-py0)*float(end[1])/100 if isinstance(end,(list,tuple)) and len(end)>=2 else sy
            kind=str(meta.get('visual_type',ev.get('event_type','EVENT'))).upper()
            acol={'PASS':(95,210,255),'CARRY':(255,220,90),'SHOT':(255,245,125),'PRESSURE':(235,105,135),'TACKLE':(245,175,90),'INTERCEPTION':(175,235,180)}.get(kind,(255,255,255))
            d.line((sx,sy,ex,ey),fill=acol,width=6)
            d.ellipse((sx-10,sy-10,sx+10,sy+10),fill=(255,255,255),outline=acol,width=3)
            d.ellipse((ex-12,ey-12,ex+12,ey+12),outline=acol,width=4)
    # Side panel
    rounded(d,(1170,125,1550,855),24,fill=(10,29,50),outline=(45,90,130),w=2)
    d.text((1200,155),'MATCH CENTER',font=font(25,True),fill=(170,205,235))
    info=[('Счёт',f'{home_score} : {away_score}'),('Время',f'{minute}′'),('Контроль',f'{int(home_control*100)}% / {100-int(home_control*100)}%'),('Прессинг',f'{pressure_home} / {pressure_away}'),('Фаза',phase),('Схема',f'{formation_home} / {formation_away}')]
    for i,(k,v) in enumerate(info):
        y=215+i*70; d.text((1200,y),k,font=font(14,True),fill=(105,155,195)); d.text((1200,y+25),v,font=font(19,True),fill=(240,245,250))
    d.text((1200,655),'ПОСЛЕДНИЕ СОБЫТИЯ',font=font(15,True),fill=(115,190,225))
    y=690
    for e in (events or [])[-4:]:
        desc=str(e.get('description',e.get('text','')))[:38]
        d.text((1200,y),f"{e.get('minute','')}′ {desc}",font=font(12),fill=(225,235,245)); y+=34
    if out is None:
        key=f'{home}|{away}|{formation_home}|{formation_away}|{phase}|{minute}|{home_score}|{away_score}|{pressure_home}|{pressure_away}'
        out=ASSET/f"dynamic_match_{hashlib.sha256(key.encode()).hexdigest()[:14]}.png"
    im.save(out,optimize=True)
    return out


def event_replay_screen(home, away, formation_home, formation_away, event, event_index=1, total_events=1,
                        home_score=0, away_score=0, out=None, size=(1600,900)):
    """Render one concrete match action from MatchEvent.metadata on the tactical pitch."""
    from app.game.board import FORMATION_SLOTS
    if formation_home not in FORMATION_SLOTS or formation_away not in FORMATION_SLOTS:
        raise ValueError('Unsupported formation')
    im=Image.new('RGB',size,(4,12,22)); d=ImageDraw.Draw(im)
    d.text((45,25),f'{home}  {home_score}:{away_score}  {away}',font=font(30,True),fill=(245,248,252))
    d.text((45,70),f"{event.get('minute','')}′ · {event.get('type','EVENT')}",font=font(18,True),fill=(110,200,235))
    px0,py0,px1,py1=45,125,1125,855
    d.rounded_rectangle((px0,py0,px1,py1),radius=24,fill=(10,82,52),outline=(115,205,150),width=3)
    for frac in (.2,.4,.6,.8):
        x=px0+(px1-px0)*frac; d.line((x,py0,x,py1),fill=(80,165,110),width=2)
    d.line(((px0+px1)//2,py0,(px0+px1)//2,py1),fill=(205,235,215),width=3)
    cx=(px0+px1)//2; cy=(py0+py1)//2; d.ellipse((cx-90,cy-90,cx+90,cy+90),outline=(205,235,215),width=3)
    d.rectangle((px0,py0+220,px0+120,py1-220),outline=(205,235,215),width=3)
    d.rectangle((px1-120,py0+220,px1,py1-220),outline=(205,235,215),width=3)

    def draw_team(formation, mirror, fill):
        pts=[]
        for i,(slot,x,y) in enumerate(FORMATION_SLOTS[formation]):
            depth=100-x if mirror else x
            px=px0+(px1-px0)*depth/100; py=py0+(py1-py0)*y/100
            pts.append((px,py,slot,i+1))
        for px,py,slot,num in pts:
            d.ellipse((px-17,py-17,px+17,py+17),fill=fill,outline=(255,255,255),width=2)
            text_center(d,(px,py),str(num),font(9,True),(255,255,255))
        return pts
    hp=draw_team(formation_home,False,(35,145,245)); ap=draw_team(formation_away,True,(235,70,85))

    meta=event.get('metadata') or {}
    start=meta.get('start'); end=meta.get('end')
    if isinstance(start,(list,tuple)) and len(start)>=2:
        sx=px0+(px1-px0)*float(start[0])/100; sy=py0+(py1-py0)*float(start[1])/100
        ex=px0+(px1-px0)*float(end[0])/100 if isinstance(end,(list,tuple)) and len(end)>=2 else sx
        ey=py0+(py1-py0)*float(end[1])/100 if isinstance(end,(list,tuple)) and len(end)>=2 else sy
        kind=str(meta.get('visual_type',event.get('type','EVENT'))).upper()
        line_fill={'PASS':(95,210,255),'CARRY':(255,220,90),'SHOT':(255,245,125),'PRESSURE':(235,105,135),'TACKLE':(245,175,90),'INTERCEPTION':(175,235,180)}.get(kind,(255,255,255))
        if kind in {'PASS','CARRY','SHOT'}:
            d.line((sx,sy,ex,ey),fill=line_fill,width=8)
            ang=math.atan2(ey-sy,ex-sx); ah=19
            for a in (ang+2.65,ang-2.65):
                d.line((ex,ey,ex+ah*math.cos(a),ey+ah*math.sin(a)),fill=line_fill,width=5)
        else:
            d.ellipse((sx-18,sy-18,sx+18,sy+18),outline=line_fill,width=5)
            d.line((sx,sy,ex,ey),fill=line_fill,width=5)
            d.ellipse((ex-14,ey-14,ex+14,ey+14),outline=line_fill,width=4)
        d.ellipse((sx-8,sy-8,sx+8,sy+8),fill=(255,255,255))
        if kind=='SHOT' and meta.get('result')=='GOAL':
            d.ellipse((ex-24,ey-24,ex+24,ey+24),outline=(255,225,90),width=7)
            text_center(d,(ex,ey),'⚽',font(24,True),(255,245,180))
        elif kind=='SHOT':
            d.ellipse((ex-18,ey-18,ex+18,ey+18),outline=(255,255,255),width=4)
            text_center(d,(ex,ey),str(meta.get('result','SHOT')),font(11,True),(255,255,255))

    # Side panel
    rounded(d,(1170,125,1550,855),24,fill=(10,29,50),outline=(45,90,130),w=2)
    d.text((1200,155),'EVENT REPLAY',font=font(25,True),fill=(170,205,235))
    d.text((1200,205),f'Кадр {event_index}/{total_events}',font=font(14,True),fill=(105,155,195))
    desc=str(event.get('text') or event.get('description') or 'Тактическое действие')
    d.text((1200,245),desc[:38],font=font(18,True),fill=(242,246,250))
    d.text((1200,300),f"Тип · {event.get('event_type',event.get('type','EVENT'))}",font=font(14,True),fill=(105,155,195))
    d.text((1200,327),f"Команда · {event.get('team','')}",font=font(17,True),fill=(240,245,250))
    if event.get('player'): d.text((1200,370),f"Игрок · {str(event['player'])[:28]}",font=font(16,True),fill=(240,245,250))
    if event.get('secondary_player'): d.text((1200,400),f"Цель · {str(event['secondary_player'])[:27]}",font=font(15),fill=(195,215,230))
    if meta.get('zone'): d.text((1200,445),f"Зона · {meta['zone']}",font=font(15,True),fill=(110,200,235))
    if meta.get('result'): d.text((1200,477),f"Результат · {meta['result']}",font=font(15,True),fill=(255,220,100))
    d.text((1200,555),'ЛЕГЕНДА',font=font(15,True),fill=(115,190,225))
    legend=[('PASS','Пас'),('CARRY','Продвижение'),('PRESSURE','Прессинг'),('TACKLE','Отбор'),('INTERCEPTION','Перехват'),('SHOT','Удар')]
    for i,(key,label) in enumerate(legend):
        y=595+i*38
        d.ellipse((1200,y+4,1216,y+20),fill={'PASS':(95,210,255),'CARRY':(255,220,90),'PRESSURE':(235,105,135),'TACKLE':(245,175,90),'INTERCEPTION':(175,235,180),'SHOT':(255,245,125)}[key])
        d.text((1230,y),label,font=font(14),fill=(225,235,245))
    if out is None:
        key=f'{home}|{away}|{formation_home}|{formation_away}|{event_index}|{event.get("minute")}|{event.get("type")}|{event.get("player")}'
        out=ASSET/f"event_replay_{hashlib.sha256(key.encode()).hexdigest()[:14]}.png"
    im.save(out,optimize=True)
    return out
