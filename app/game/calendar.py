from datetime import date, datetime, timedelta
from sqlalchemy import text

PRIORITY = {'LEAGUE': 0, 'CUP': 1, 'CHAMPIONS': 2}


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    return value


async def get_next_free_date(session, candidate, club_ids=None, priority=0, exact_time=False):
    """Return the first date where the supplied clubs have no scheduled match.

    Priority is kept explicit so callers can reserve higher-priority competition
    dates first: league (0), cup (1), champions (2).
    """
    current = candidate if isinstance(candidate, datetime) else _as_date(candidate)
    clubs = [int(c) for c in (club_ids or [])]
    while True:
        if clubs:
            if exact_time and isinstance(current, datetime):
                conflict = (await session.execute(text("""
                    SELECT 1 FROM matches
                    WHERE scheduled_at=CAST(:scheduled_at AS timestamptz)
                      AND (home_club_id=ANY(:clubs) OR away_club_id=ANY(:clubs))
                    LIMIT 1
                """), {'scheduled_at': current, 'clubs': clubs})).first()
            else:
                conflict = (await session.execute(text("""
                    SELECT 1 FROM matches
                    WHERE scheduled_at::date=:d
                      AND (home_club_id=ANY(:clubs) OR away_club_id=ANY(:clubs))
                    LIMIT 1
                """), {'d': _as_date(current), 'clubs': clubs})).first()
        else:
            conflict = None
        if not conflict:
            return current
        if exact_time and isinstance(current, datetime):
            current += timedelta(minutes=10)
        else:
            current += timedelta(days=1)
