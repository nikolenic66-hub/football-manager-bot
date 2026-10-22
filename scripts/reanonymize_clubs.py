"""Reassign meaningful fictional names to every club row.

Usage: python scripts/reanonymize_clubs.py [--dry-run]
Names are updated through temporary unique values to respect clubs.name's UNIQUE constraint.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text
from app.club_names import CLUB_NAMES, fictional_club_name

BATCH_SIZE = 100


async def main(dry_run: bool = False) -> int:
    from app.db import SessionLocal

    async with SessionLocal() as session:
        ids = [int(x) for x in (await session.execute(text('SELECT id FROM clubs ORDER BY id'))).scalars().all()]
        if dry_run:
            print(f'Would rename {len(ids)} clubs; {len(CLUB_NAMES)} base names available.')
            return len(ids)
        # First clear all existing unique names so swaps/overlaps cannot violate UNIQUE(name).
        for offset in range(0, len(ids), BATCH_SIZE):
            for club_id in ids[offset:offset + BATCH_SIZE]:
                await session.execute(
                    text('UPDATE clubs SET name=:name WHERE id=:id'),
                    {'name': f'__club_rename_pending_{club_id}__', 'id': club_id},
                )
            await session.commit()
        count = 0
        for offset in range(0, len(ids), BATCH_SIZE):
            batch = ids[offset:offset + BATCH_SIZE]
            for index, club_id in enumerate(batch, start=offset):
                name = fictional_club_name(index)
                short_name = ''.join(part[0] for part in name.split()[:3]).upper()[:8]
                await session.execute(
                    text('''UPDATE clubs SET name=:name, short_name=:short_name,
                            stadium_name=:stadium WHERE id=:id'''),
                    {'name': name, 'short_name': short_name,
                     'stadium': f'{name} Ground', 'id': club_id},
                )
                count += 1
            await session.commit()
    print(f'Renamed {count} clubs using {len(CLUB_NAMES)} meaningful base names.')
    return count


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    asyncio.run(main(args.dry_run))
