"""Download reusable footballer portraits from Wikimedia Commons.

Run only on a machine with internet access. Each image is selected through the
Commons API and its source/license metadata is stored in PostgreSQL.
"""
import asyncio, json, re
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from app.db import SessionLocal
from app.visual.render import player_card
from sqlalchemy import text

API='https://commons.wikimedia.org/w/api.php'
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'assets'/'portraits'
UA='FootballManagerBot/1.0 (private game; contact administrator)'

def api(params):
    req=Request(API+'?'+urlencode(params),headers={'User-Agent':UA})
    with urlopen(req,timeout=20) as r: return json.load(r)

def safe(s): return re.sub(r'[^A-Za-z0-9._-]+','_',s).strip('_')

def find_portrait(name):
    tokens=[t.casefold() for t in re.findall(r'[A-Za-zÀ-ÿ]+',name) if len(t)>2]
    data=api({'action':'query','generator':'search','gsrsearch':name,'gsrnamespace':6,'gsrlimit':12,'prop':'imageinfo','iiprop':'url|extmetadata','iiurlwidth':512,'format':'json'})
    pages=data.get('query',{}).get('pages',{}).values()
    candidates=[]
    for page in pages:
        title=str(page.get('title','')).casefold()
        if tokens and not all(t in title for t in tokens):
            continue
        info=(page.get('imageinfo') or [{}])[0]
        url=info.get('thumburl') or info.get('url')
        meta=info.get('extmetadata',{})
        mime=info.get('mime','')
        if url and mime.startswith('image/'):
            candidates.append((url, meta.get('LicenseShortName',{}).get('value'), meta.get('Artist',{}).get('value'), page.get('title')))
    return candidates[0] if candidates else None

async def main():
    OUT.mkdir(parents=True,exist_ok=True)
    async with SessionLocal() as s:
        rows=(await s.execute(text("SELECT id,first_name,last_name FROM players WHERE real_player=true AND portrait_url IS NULL ORDER BY id"))).mappings().all()
        done=0
        for r in rows:
            name=f"{r['first_name']} {r['last_name']}".strip()
            try: found=find_portrait(name)
            except Exception as e:
                print('SKIP',name,e); continue
            if not found: print('NONE',name); continue
            url,license_name,author,title=found
            ext='.jpg' if '.jpg' in url.lower() or '.jpeg' in url.lower() else '.png'
            path=OUT/f"{r['id']}_{safe(name)}{ext}"
            try:
                req=Request(url,headers={'User-Agent':UA})
                with urlopen(req,timeout=30) as src, open(path,'wb') as dst: dst.write(src.read())
            except Exception as e:
                print('DOWNLOAD_FAIL',name,e); continue
            rel=str(path.relative_to(OUT.parents[1]))
            await s.execute(text("UPDATE players SET portrait_url=:p,portrait_source=:src,portrait_license=:lic,portrait_author=:author WHERE id=:id"),{'p':rel,'src':title,'lic':license_name or 'verify-on-source','author':author or 'unknown','id':r['id']})
            # Rebuild this exact player card from the same player ID + portrait file.
            row=(await s.execute(text('SELECT * FROM players WHERE id=:id'),{'id':r['id']})).mappings().first()
            if row:
                player_card(dict(row,portrait_path=path), out=ROOT/'visual'/'cards'/f'player_{r["id"]}.png')
            await s.commit(); done+=1; print('OK',name,path)
    print('Downloaded',done)

if __name__=='__main__': asyncio.run(main())
