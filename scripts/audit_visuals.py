"""Audit the visual asset pack and report missing/corrupt files."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from PIL import Image
from app.player_catalog import REAL_PLAYERS, LEGEND_NAMES, REAL_ORDER
from app.game.board import FORMATION_SLOTS

ASSET=ROOT/'assets'/'visual'
expected_cards=1248
expected_formations=5
expected_crests=12
expected_icons=10

def valid_pngs(folder):
    files=sorted(folder.glob('*.png'))
    bad=[]
    for f in files:
        try:
            with Image.open(f) as im:
                im.verify()
        except Exception as exc:
            bad.append((f.name,str(exc)))
    return files,bad

def main():
    checks=[
        ('cards',ASSET/'cards',expected_cards,'player_'),
        ('formations',ASSET/'formations',expected_formations,''),
        ('clubs',ASSET/'clubs',expected_crests,'club_'),
        ('icons',ASSET/'icons',expected_icons,''),
    ]
    failed=False
    for name,folder,count,prefix in checks:
        files,bad=valid_pngs(folder)
        missing=count-len(files)
        ok=(len(files)==count and not bad)
        print(f'{name}: {len(files)}/{count} PNGs | OK={ok}')
        if bad:
            print('  corrupt:',bad[:5])
        if missing:
            print('  missing:',missing)
        failed |= not ok
    # Card IDs must use exactly the same ordering as app.seed.REAL_ORDER.
    mismatch=[]
    for i,(first,last,nat,pos) in enumerate(REAL_ORDER,1):
        card=ASSET/'cards'/f'player_{i}.png'
        if not card.exists(): mismatch.append((i,'missing',f'{first} {last}'))
    print(f'real players catalog: {len(REAL_PLAYERS)}')
    print(f'visual card-id coverage: {len(REAL_ORDER)-len(mismatch)}/{len(REAL_ORDER)}')
    if mismatch:
        print('  card mismatches/missing:',mismatch[:5]); failed=True
    # Formation semantic audit: exact 11 slots and exact positional counts.
    expected_counts={
      '4-3-3': {'GK':1,'LB':1,'CB':2,'RB':1,'CM':2,'DM':1,'LW':1,'ST':1,'RW':1},
      '4-4-2': {'GK':1,'LB':1,'CB':2,'RB':1,'LM':1,'CM':2,'RM':1,'ST':2},
      '4-2-3-1': {'GK':1,'LB':1,'CB':2,'RB':1,'DM':2,'LW':1,'AM':1,'RW':1,'ST':1},
      '3-5-2': {'GK':1,'CB':3,'LM':1,'CM':2,'DM':1,'RM':1,'ST':2},
      '5-3-2': {'GK':1,'LB':1,'CB':3,'RB':1,'CM':2,'DM':1,'ST':2},
    }
    for f,expected in expected_counts.items():
        got={}
        for slot,_,_ in FORMATION_SLOTS[f]: got[slot]=got.get(slot,0)+1
        ok=got==expected and len(FORMATION_SLOTS[f])==11
        print(f'formation {f}: {got} | OK={ok}')
        failed |= not ok
    print(f'legend catalog: {len(LEGEND_NAMES)}')
    print(f'legend names matched: {len({f"{a} {b}".strip() for a,b,_,_ in REAL_PLAYERS} & LEGEND_NAMES)}')
    portraits=ROOT/'assets'/'portraits'
    local_portraits=[x for x in portraits.glob('*') if x.is_file() and x.name!='.gitkeep'] if portraits.exists() else []
    print(f'local portraits: {len(local_portraits)}')
    manifest=ASSET/'cards_manifest.json'
    formation_manifest=ASSET/'formation_manifest.json'
    manifest_ok=manifest.exists() and formation_manifest.exists()
    print(f'cards manifest: {manifest.exists()}')
    print(f'formation manifest: {formation_manifest.exists()}')
    failed |= not manifest_ok
    if failed:
        raise SystemExit(1)
    print('VISUAL AUDIT: PASS')

if __name__=='__main__': main()
