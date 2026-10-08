"""Download and normalize approved Wikimedia Commons portrait sources."""
from pathlib import Path
import asyncio, json, io
from urllib.request import Request, urlopen
from PIL import Image, ImageOps
from sqlalchemy import text
from app.db import SessionLocal

ROOT=Path(__file__).resolve().parents[1]
MANIFEST=ROOT/'portraits_manifest.json'
OUT=ROOT/'assets'/'portraits'; OUT.mkdir(parents=True,exist_ok=True)
UA='football-manager-bot/1.0 (+Wikimedia Commons portrait loader)'


def download_bytes(url):
    req=Request(url,headers={'User-Agent':UA})
    with urlopen(req,timeout=8) as r:
        return r.read()


def normalize(data):
    with Image.open(io.BytesIO(data)) as src:
        src=src.convert('RGB')
        fitted=ImageOps.contain(src,(256,256),Image.Resampling.LANCZOS)
        canvas=Image.new('RGB',(256,256),(18,24,32))
        canvas.paste(fitted,((256-fitted.width)//2,(256-fitted.height)//2))
        return canvas

async def main():
    manifest=json.loads(MANIFEST.read_text(encoding='utf-8'))
    downloaded=skipped=errors=0
    async with SessionLocal() as s:
        for item in manifest:
            pid=int(item['player_id']); url=item.get('url')
            path=OUT/f'{pid}_real.png'
            if path.exists():
                skipped+=1; continue
            if not url:
                skipped+=1; continue
            try:
                data=await asyncio.to_thread(download_bytes,url)
                image=normalize(data)
                image.save(path,format='PNG',optimize=True)
                await s.execute(text('''UPDATE players SET portrait_url=:url,portrait_source=:source,portrait_license=:license WHERE id=:id'''),{
                    'url':str(path.relative_to(ROOT)),'source':item.get('source') or 'wikimedia_commons','license':item.get('license') or 'verify-on-source','id':pid})
                await s.commit()
                downloaded+=1
                print(f'OK {pid} {item.get("name")}')
            except Exception as exc:
                errors+=1
                print(f'ERROR {pid} {item.get("name")}: {exc}')
        await s.commit()
    print(f'Real portraits: downloaded={downloaded} skipped={skipped} errors={errors} total={len(manifest)}')

if __name__=='__main__':
    asyncio.run(main())
