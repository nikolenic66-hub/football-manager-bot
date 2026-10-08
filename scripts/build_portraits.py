"""Generate deterministic 256x256 fallback portraits for all seeded players."""
from pathlib import Path
import asyncio, hashlib, re
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import text
from app.db import SessionLocal

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'assets'/'portraits'
OUT.mkdir(parents=True,exist_ok=True)
FONT='/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
FONT_B='/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
PALETTE={
    'BASE':((45,55,68),(105,130,155)),
    'RARE':((18,70,42),(70,205,105)),
    'EPIC':((58,25,88),(190,90,245)),
    'LEGENDARY':((78,55,12),(235,190,65)),
}

def _font(size,bold=False):
    return ImageFont.truetype(FONT_B if bold else FONT,size)

def _initials(first,last):
    parts=[p for p in re.split(r"[^\wÀ-ÿ]+",f'{first} {last}'.strip(),flags=re.UNICODE) if p]
    return ''.join(p[0] for p in parts[:2]).upper() or '?'

def _make(row):
    pid=int(row['id']); rarity=row.get('rarity') or 'BASE'
    bg,accent=PALETTE.get(rarity,PALETTE['BASE'])
    digest=hashlib.sha256(f'football-manager-avatar:{pid}'.encode()).digest()
    # Deterministic per-player variation without relying on global randomness.
    shift=(digest[0]%25)-12
    bg=tuple(max(0,min(255,c+shift)) for c in bg)
    accent=tuple(max(0,min(255,c+((digest[1]%21)-10))) for c in accent)
    im=Image.new('RGB',(256,256),bg); d=ImageDraw.Draw(im)
    d.rectangle((0,0,255,255),outline=accent,width=5)
    d.ellipse((20,20,236,236),outline=accent,width=2)
    # shoulders
    d.rounded_rectangle((42,158,214,246),radius=55,fill=(204,208,214),outline=accent,width=4)
    # neck
    d.rectangle((106,133,150,177),fill=(216,219,223),outline=accent,width=3)
    # head
    d.ellipse((72,45,184,164),fill=(222,225,229),outline=accent,width=4)
    # simple hair/cap silhouette, varied deterministically by id
    hair=30+(digest[2]%35)
    d.arc((78,40,178,105),180,360,fill=(55,60,68),width=max(6,hair//6))
    d.ellipse((105,92,112,99),fill=(60,65,72)); d.ellipse((144,92,151,99),fill=(60,65,72))
    d.arc((112,103,144,126),10,170,fill=(90,95,102),width=3)
    initials=_initials(row.get('first_name',''),row.get('last_name',''))
    box=d.textbbox((0,0),initials,font=_font(34,True)); tw=box[2]-box[0]; th=box[3]-box[1]
    d.rounded_rectangle((76,181,180,231),radius=16,fill=bg,outline=accent,width=2)
    d.text(((256-tw)/2,187-(box[1]),),initials,font=_font(34,True),fill=accent)
    d.text((10,8),str(pid),font=_font(13,True),fill=accent)
    return im

async def main():
    created=skipped=0
    async with SessionLocal() as s:
        rows=(await s.execute(text('SELECT id,first_name,last_name,position,rarity FROM players ORDER BY id'))).mappings().all()
    for row in rows:
        path=OUT/f"{int(row['id'])}_avatar.png"
        if path.exists():
            skipped+=1; continue
        _make(row).save(path,format='PNG',optimize=True)
        created+=1
    print(f'Portrait placeholders: created={created} skipped={skipped} total={len(rows)}')

if __name__=='__main__':
    asyncio.run(main())
