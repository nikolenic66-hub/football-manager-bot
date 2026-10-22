"""Audit or reassign fictional player names without changing gameplay data.

Preview: python scripts/reanonymize_players.py --dry-run
Apply:   python scripts/reanonymize_players.py
Only first_name and last_name are changed. IDs, stats, ownership, contracts,
rarity, market value, nationality, and all other fields remain untouched.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.player_reanonymize import reanonymize_players


async def main(dry_run: bool = False) -> int:
    from app.db import SessionLocal

    async with SessionLocal() as session:
        try:
            report = await reanonymize_players(session, dry_run=dry_run)
        except Exception:
            await session.rollback()
            raise

    print(f"Players: {report['players_found']}")
    print(f"Current duplicate full names: {report['duplicate_full_names_found']}")
    print(f"Current distinct surnames: {report['distinct_surnames_before']}")
    print(f"Planned duplicate full names: {report['planned_duplicate_full_names']}")
    print(f"Planned distinct surnames: {report['planned_distinct_surnames']}")
    print(f"Name catalog: {report['first_names_in_catalog']} first names x "
          f"{report['surnames_in_catalog']} surnames")
    if dry_run:
        print('DRY RUN: database was not changed.')
    else:
        print(f"Renamed {report['players_updated']} players. Gameplay data was not modified.")
    return report['players_found']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    asyncio.run(main(args.dry_run))
