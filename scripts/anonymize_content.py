"""Idempotently replace real-world player/club content with fictional data.

Run with the application's configured DATABASE_URL. Updates are committed in
batches of 100 records; --dry-run reports row counts without changing data.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

BATCH_SIZE = 100
from app.player_names import FIRST, LAST, fictional_player_name
from app.club_names import fictional_club_name


async def table_columns(session, table: str) -> set[str]:
    rows = (await session.execute(text('''
        SELECT column_name FROM information_schema.columns
        WHERE table_schema=current_schema() AND table_name=:table
    '''), {'table': table})).scalars().all()
    return set(rows)


async def anonymize_players(session, dry_run: bool) -> int:
    columns = await table_columns(session, 'players')
    required = {'id', 'first_name', 'last_name'}
    if not required.issubset(columns):
        raise RuntimeError(f'players table is missing required columns: {sorted(required-columns)}')
    ids = (await session.execute(text('SELECT id FROM players ORDER BY id'))).scalars().all()
    count = 0
    for offset in range(0, len(ids), BATCH_SIZE):
        batch = [int(x) for x in ids[offset:offset+BATCH_SIZE]]
        if dry_run:
            count += len(batch)
            continue
        for row_index, player_id in enumerate(batch, start=offset + 1):
            first, last = fictional_player_name(row_index)
            assignments = ['first_name=:first', 'last_name=:last']
            params = {'first': first, 'last': last, 'id': player_id}
            for col in ('portrait_url', 'image_url', 'portrait_source', 'portrait_license',
                        'portrait_author', 'portrait_url_hash'):
                if col in columns:
                    assignments.append(f'{col}=NULL')
            for col in ('real_player', 'is_real'):
                if col in columns:
                    assignments.append(f'{col}=FALSE')
            if 'data_source' in columns:
                assignments.append("data_source='synthetic'")
            await session.execute(text(
                f"UPDATE players SET {', '.join(assignments)} WHERE id=:id"
            ), params)
            count += 1
        await session.commit()
    return count


async def anonymize_clubs(session, dry_run: bool) -> int:
    columns = await table_columns(session, 'clubs')
    required = {'id', 'name', 'short_name', 'stadium_name'}
    if not required.issubset(columns):
        raise RuntimeError(f'clubs table is missing required columns: {sorted(required-columns)}')
    ids = (await session.execute(text('SELECT id FROM clubs ORDER BY id'))).scalars().all()
    count = len(ids) if dry_run else 0
    if dry_run:
        return count
    # Rename every row to a unique temporary value first, preventing conflicts
    # with clubs whose current name happens to match a future generated name.
    for offset in range(0, len(ids), BATCH_SIZE):
        batch = [int(x) for x in ids[offset:offset+BATCH_SIZE]]
        for club_id in batch:
            await session.execute(text('UPDATE clubs SET name=:n WHERE id=:id'),
                                  {'n': f'__fictional_pending_{club_id}__', 'id': club_id})
        await session.commit()
    for offset in range(0, len(ids), BATCH_SIZE):
        batch = [int(x) for x in ids[offset:offset+BATCH_SIZE]]
        for row_index, club_id in enumerate(batch, start=offset):
            name = fictional_club_name(row_index)
            short = ''.join(part[0] for part in name.split()[:3]).upper()[:8]
            assignments = ['name=:name', 'short_name=:short', 'stadium_name=:stadium']
            params = {'name': name, 'short': short,
                      'stadium': f'{name} Ground', 'id': club_id}
            for col in ('logo', 'logo_url'):
                if col in columns:
                    assignments.append(f'{col}=NULL')
            await session.execute(text(
                f"UPDATE clubs SET {', '.join(assignments)} WHERE id=:id"
            ), params)
            count += 1
        await session.commit()
    return count


async def main(dry_run: bool = False):
    from app.db import SessionLocal
    async with SessionLocal() as session:
        players = await anonymize_players(session, dry_run)
        clubs = await anonymize_clubs(session, dry_run)
    mode = 'Would anonymize' if dry_run else 'Anonymized'
    print(f'{mode}: {players} player rows and {clubs} club rows (batch size {BATCH_SIZE}).')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', help='Count records without modifying the database.')
    args = parser.parse_args()
    asyncio.run(main(args.dry_run))
