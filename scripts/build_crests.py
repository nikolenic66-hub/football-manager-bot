"""Build deterministic original club crests from clubs.name.

Only Pillow drawing is used. No real club, league or brand artwork is loaded.
"""
from pathlib import Path
import asyncio
import hashlib
import re
import sys

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'assets' / 'visual' / 'crests'
OUT.mkdir(parents=True, exist_ok=True)
FONT_B = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'

PALETTE = (
    (30, 96, 150), (34, 126, 105), (44, 88, 150), (108, 62, 145),
    (154, 72, 62), (184, 116, 38), (48, 115, 135), (78, 91, 145),
    (42, 126, 76), (125, 70, 112),
)
IGNORE = {'FC', 'FK', 'CF', 'SC', 'AC', 'AS', 'RC', 'AFC', 'IF', 'BK', 'SK', 'CD'}


def initials(name: str) -> str:
    words = [w for w in re.findall(r"[^\W_]+", str(name).upper(), flags=re.UNICODE) if w not in IGNORE]
    if not words:
        words = [str(name).upper() or 'CLUB']
    if len(words) >= 2:
        return ''.join(w[0] for w in words[:3])
    word = words[0]
    letters = [c for c in word if c.isalnum()]
    if len(letters) == 1:
        return letters[0]
    vowels=set('AEIOUАЕЁИОУЫЭЮЯ')
    for letter in letters[1:]:
        if letter not in vowels:
            return letters[0] + letter
    return letters[0] + letters[-1]


def _font_for(text: str) -> ImageFont.FreeTypeFont:
    size = 82 if len(text) == 1 else 68 if len(text) == 2 else 57
    return ImageFont.truetype(FONT_B, size)


def crest_image(name: str, size=(256, 256)) -> Image.Image:
    digest = hashlib.sha256(str(name).encode('utf-8')).digest()
    bg = PALETTE[digest[0] % len(PALETTE)]
    dark = tuple(max(0, int(c * 0.45)) for c in bg)
    light = tuple(min(255, int(c * 1.22 + 8)) for c in bg)
    lum = 0.2126 * bg[0] + 0.7152 * bg[1] + 0.0722 * bg[2]
    ink = (22, 25, 30, 255) if lum > 145 else (255, 255, 255, 255)

    im = Image.new('RGBA', size, (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    cx = size[0] // 2
    shield = [(34, 22), (222, 22), (222, 92), (211, 164), (190, 204),
              (160, 232), (128, 246), (96, 232), (66, 204), (45, 164), (34, 92)]
    inner = [(45, 35), (211, 35), (211, 91), (201, 156), (181, 191),
             (156, 216), (128, 229), (100, 216), (75, 191), (55, 156), (45, 91)]
    d.polygon(shield, fill=dark + (255,), outline=(10, 15, 22, 220))
    d.polygon(inner, fill=bg + (255,), outline=light + (210,))
    # Thin central stripe: decorative and independent of any real crest.
    d.polygon([(112, 35), (144, 35), (158, 229), (128, 240), (98, 229)], fill=light + (65,))
    d.line([(47, 91), (209, 91)], fill=(255, 255, 255, 70), width=2)

    text = initials(name)
    f = _font_for(text)
    box = d.textbbox((0, 0), text, font=f)
    tw, th = box[2] - box[0], box[3] - box[1]
    d.text((cx - tw / 2, 132 - th / 2 - box[1]), text, font=f, fill=ink,
           stroke_width=2, stroke_fill=(0, 0, 0, 90) if ink[0] > 200 else (255, 255, 255, 80))
    return im


async def main() -> int:
    sys.path.insert(0, str(ROOT))
    from app.db import SessionLocal
    from sqlalchemy import text

    created = skipped = 0
    async with SessionLocal() as s:
        clubs = (await s.execute(text('SELECT id, name FROM clubs ORDER BY id'))).mappings().all()

    for club in clubs:
        path = OUT / f"{int(club['id'])}.png"
        if path.exists():
            skipped += 1
            continue
        crest_image(str(club['name'])).save(path, format='PNG', optimize=True)
        created += 1

    print(f'Built {created} club crests, skipped {skipped}.')
    return 0


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
