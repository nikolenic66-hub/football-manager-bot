"""Safe player-name reassignment service shared by CLI and admin bot command."""
from __future__ import annotations

from collections import Counter

from sqlalchemy import text

from .player_names import FIRST, LAST, fictional_player_name


def _key(first: object, last: object) -> tuple[str, str]:
    return str(first or '').strip().casefold(), str(last or '').strip().casefold()


async def reanonymize_players(session, dry_run: bool = False) -> dict[str, int]:
    """Reassign fictional names only; never recreates players or gameplay data.

    The caller owns the session/transaction. With ``dry_run=True`` no writes occur.
    On apply, only ``players.first_name`` and ``players.last_name`` are updated.
    """
    rows = (await session.execute(text(
        'SELECT id, first_name, last_name FROM players ORDER BY id'
    ))).mappings().all()
    current_names = [_key(row['first_name'], row['last_name']) for row in rows]
    current_counts = Counter(current_names)
    current_full_duplicates = sum(count - 1 for count in current_counts.values() if count > 1)
    current_duplicate_groups = sum(1 for count in current_counts.values() if count > 1)
    current_surnames = len({last for _, last in current_names if last})
    planned = [fictional_player_name(index) for index in range(1, len(rows) + 1)]
    planned_keys = [_key(first, last) for first, last in planned]
    planned_full_duplicates = len(planned_keys) - len(set(planned_keys))
    planned_surnames = len({last for _, last in planned_keys if last})

    if planned_full_duplicates:
        raise RuntimeError(
            'Name generator produced duplicate full names; refusing to update database.'
        )

    if not dry_run and rows:
        updates = [
            {'first': first, 'last': last, 'id': int(row['id'])}
            for row, (first, last) in zip(rows, planned)
        ]
        await session.execute(text(
            'UPDATE players SET first_name=:first, last_name=:last WHERE id=:id'
        ), updates)
        await session.commit()

    return {
        'players_found': len(rows),
        'players_updated': 0 if dry_run else len(rows),
        'duplicate_full_names_found': current_full_duplicates,
        'duplicate_name_groups_found': current_duplicate_groups,
        'distinct_surnames_before': current_surnames,
        'planned_duplicate_full_names': planned_full_duplicates,
        'planned_distinct_surnames': planned_surnames,
        'first_names_in_catalog': len(FIRST),
        'surnames_in_catalog': len(LAST),
    }
