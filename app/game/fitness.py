"""Daily, idempotent player fitness recovery."""
from sqlalchemy import text


async def recover_daily_fitness(session) -> int:
    """Apply +15 fitness for match days and +20 for days without appearances.

    The recovery date is stored per club-player row so the live worker can call
    this every loop without granting repeated recovery during the same day.
    Starters are identified through match_lineups; substitutes through persisted
    SUBSTITUTION events.
    """
    rows = (await session.execute(text('''
        UPDATE club_players cp
        SET fitness = LEAST(100, cp.fitness + COALESCE((
                SELECT SUM(CASE WHEN
                    EXISTS (
                        SELECT 1 FROM match_lineups ml
                        JOIN matches m ON m.id=ml.match_id
                        WHERE ml.club_id=cp.club_id AND ml.player_id=cp.player_id
                          AND m.status='FINISHED'
                          AND m.finished_at::date = days.recovery_day::date
                    ) OR EXISTS (
                        SELECT 1 FROM match_events e
                        JOIN matches m ON m.id=e.match_id
                        WHERE e.club_id=cp.club_id AND e.player_id=cp.player_id
                          AND e.event_type='SUBSTITUTION' AND m.status='FINISHED'
                          AND m.finished_at::date = days.recovery_day::date
                    ) THEN 15 ELSE 20 END)
                FROM generate_series(
                    cp.fitness_recovered_on + 1, CURRENT_DATE - 1, INTERVAL '1 day'
                ) AS days(recovery_day)
            ), 0)::int),
            fitness_recovered_on=CURRENT_DATE - 1
        WHERE cp.fitness_recovered_on < CURRENT_DATE - 1
        RETURNING cp.player_id
    '''))).scalars().all()
    return len(rows)
