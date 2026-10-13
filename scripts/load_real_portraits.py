"""Download and normalize approved Wikimedia Commons portrait sources."""
from pathlib import Path
from urllib.parse import urlparse
import hashlib
import asyncio, json, io, logging
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from PIL import Image, ImageOps
from sqlalchemy import text
from app.db import SessionLocal

ROOT=Path(__file__).resolve().parents[1]
MANIFEST=ROOT/'portraits_manifest.json'
OUT=ROOT/'assets'/'portraits'; OUT.mkdir(parents=True,exist_ok=True)
UA='football-manager-bot/1.0 (https://github.com/nikolenic66-hub/football-manager-bot)'
logger=logging.getLogger(__name__)


def download_bytes(url):
    parsed=urlparse(url)
    if parsed.scheme != 'https' or parsed.netloc != 'upload.wikimedia.org':
        raise ValueError('portrait URL must use https://upload.wikimedia.org')
    req=Request(url,headers={'User-Agent':UA})
    with urlopen(req,timeout=8) as r:
        data=r.read()
    if len(data) <= 5 * 1024:
        raise ValueError(f'portrait file is too small: {len(data)} bytes')
    return data


def normalize(data):
    with Image.open(io.BytesIO(data)) as src:
        src=src.convert('RGB')
        fitted=ImageOps.contain(src,(256,256),Image.Resampling.LANCZOS)
        canvas=Image.new('RGB',(256,256),(18,24,32))
        canvas.paste(fitted,((256-fitted.width)//2,(256-fitted.height)//2))
        return canvas

async def main():
    logger.info('Portrait real: starting')
    downloaded=skipped=errors=0
    try:
        if not MANIFEST.exists():
            raise FileNotFoundError(f'Manifest not found: {MANIFEST}')
        manifest=json.loads(MANIFEST.read_text(encoding='utf-8'))
        async with SessionLocal() as s:
            for item in manifest:
                pid=int(item['player_id']); url=item.get('url')
                path=OUT/f'{pid}_real.png'
                if not url:
                    skipped+=1; continue
                url_hash=hashlib.sha256(url.encode('utf-8')).hexdigest()
                row=(await s.execute(text('SELECT portrait_url_hash FROM players WHERE id=:id'),{'id':pid})).first()
                stored_hash=row[0] if row else None
                if path.exists() and stored_hash == url_hash:
                    skipped+=1; continue
                try:
                    data=await asyncio.to_thread(download_bytes,url)
                    image=normalize(data)
                    image.save(path,format='PNG',optimize=True)
                    await s.execute(text('''UPDATE players SET portrait_url=:url,portrait_source=:source,portrait_license=:license,portrait_url_hash=:url_hash WHERE id=:id'''),{
                        'url':str(path.relative_to(ROOT)),'source':item.get('source') or 'wikimedia_commons','license':item.get('license') or 'verify-on-source','url_hash':url_hash,'id':pid})
                    await s.commit()
                    downloaded+=1
                    logger.info('Portrait real downloaded: player_id=%s name=%s',pid,item.get('name'))
                except HTTPError as exc:
                    errors+=1
                    logger.warning('Portrait real HTTP %s: player_id=%s url=%s',exc.code,pid,url)
                except Exception as exc:
                    errors+=1
                    logger.exception('Portrait real error: player_id=%s url=%s error=%s',pid,url,exc)
            logger.info('Portrait real: downloaded=%s skipped=%s errors=%s total=%s',downloaded,skipped,errors,len(manifest))
    except Exception:
        logger.exception('Portrait real: fatal error')
        raise

if __name__=='__main__':
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(main())
    except Exception:
        logger.exception('Portrait real: process failed')
        raise
