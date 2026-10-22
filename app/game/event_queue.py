import json
import hashlib
import logging
from datetime import datetime, timezone

from sqlalchemy import text

logger = logging.getLogger(__name__)


class EventDeferred(RuntimeError):
    """A due event must be retried after its prerequisites become terminal."""


EVENT_TYPES = {
    'MATCH_LEAGUE',
    'SPONSOR_PAYMENT',
    'SALARY_PAYMENT',
    'BATTLE_PASS_END',
    'SEASON_START',
    'SEASON_END',
    'PROMOTION_RELEGATION',
    'CONTRACT_WINDOW_OPEN',
    'CONTRACT_WINDOW_CLOSE',
}


def _event_season_id(payload):
    value = payload.get('season_id')
    if value is None:
        raise ValueError('season_id is required in event payload')
    return int(value)


async def queue_event(session, event_type, scheduled_at, payload, idempotency_key=None):
    event_type = str(event_type).upper()
    if event_type not in EVENT_TYPES:
        raise ValueError(f'Unknown season event type: {event_type}')
    payload = dict(payload or {})
    season_id = _event_season_id(payload)
    if idempotency_key is None:
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        idempotency_key = hashlib.sha256(
            f'{season_id}:{event_type}:{scheduled_at.isoformat() if hasattr(scheduled_at, "isoformat") else scheduled_at}:{canonical}'.encode('utf-8')
        ).hexdigest()
    row = (await session.execute(text("""
        INSERT INTO season_events(season_id,event_type,scheduled_at,payload,status,idempotency_key)
        VALUES(:season_id,:event_type,CAST(:scheduled_at AS timestamptz),CAST(:payload AS jsonb),'PENDING',:idempotency_key)
        ON CONFLICT (idempotency_key) DO UPDATE SET id = season_events.id
        RETURNING id
    """), {
        'season_id': season_id,
        'event_type': event_type,
        'scheduled_at': scheduled_at,
        'payload': json.dumps(payload, ensure_ascii=False),
        'idempotency_key': str(idempotency_key),
    })).scalar_one()
    return int(row)


async def _process_match_event(session, event_type, payload):
    if event_type == 'MATCH_LEAGUE':
        from app.services import start_round_halftime
        match_id = int(payload['match_id'])
        row = (await session.execute(text("""
            SELECT m.league_id,m.round,m.status,l.current_round,l.status AS league_status
            FROM matches m
            LEFT JOIN leagues l ON l.id=m.league_id
            WHERE m.id=:m FOR UPDATE OF m
        """), {'m': match_id})).mappings().first()
        if not row or str(row['status']).upper() in ('LIVE', 'FINISHED', 'FAILED'):
            return
        if row['league_id'] is None or row['league_status'] != 'ACTIVE':
            logger.info('Skipping league match event=%s: league is missing or inactive', match_id)
            return

        league_id = int(row['league_id'])
        round_no = int(row['round'])
        current_round = int(row['current_round'] or 0)
        if round_no > 1:
            previous = (await session.execute(text("""
                SELECT COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE UPPER(COALESCE(status,'')) NOT IN ('FINISHED','FAILED')) AS pending
                FROM matches
                WHERE league_id=:l AND competition_type='LEAGUE' AND round=:r
            """), {'l': league_id, 'r': round_no - 1})).mappings().one()
            if int(previous['total'] or 0) == 0 or int(previous['pending'] or 0) > 0:
                message = (f'league={league_id} round={round_no} waiting for previous '
                           f'round={round_no - 1}: total={previous["total"]} pending={previous["pending"]}')
                logger.warning('Deferring MATCH_LEAGUE event match=%s: %s', match_id, message)
                raise EventDeferred(message)

        if round_no != current_round:
            if round_no > current_round:
                message = f'league={league_id} event_round={round_no} current_round={current_round}'
                logger.warning('Deferring MATCH_LEAGUE event match=%s until the league round advances: %s', match_id, message)
                raise EventDeferred(message)
            logger.info('Ignoring obsolete MATCH_LEAGUE event match=%s round=%s current_round=%s',
                        match_id, round_no, current_round)
            return

        await start_round_halftime(session, league_id, round_no)
        return
    if event_type in {'MATCH_CUP', 'MATCH_CHAMPIONS'}:
        # Legacy tournament events are intentionally not simulated anymore.
        # Migration 072 cancels queued entries; this guard protects any stale
        # entry created by an old worker or an idempotent retry.
        logger.info('Ignoring disabled tournament event type=%s', event_type)
        return


async def _process_finance_event(session, event_type, payload):
    if event_type == 'BATTLE_PASS_END':
        await session.execute(text("""
            UPDATE season_pass SET status='FINISHED'
            WHERE id=:id AND status='ACTIVE'
        """), {'id': int(payload['season_pass_id'])})
        return
    from app.game.economy import sponsor_income, wage_bill
    from app.services import record_financial_transaction
    club_id = int(payload['club_id'])
    club = (await session.execute(text("""
        SELECT id,sponsor_level,reputation FROM clubs WHERE id=:c FOR UPDATE
    """), {'c': club_id})).mappings().first()
    if not club:
        raise ValueError('Club not found')
    if event_type == 'SPONSOR_PAYMENT':
        amount = int(sponsor_income(club['sponsor_level'], float(club['reputation'] or 0)))
        await record_financial_transaction(session, club_id, 'SPONSOR', amount, 'Сезонная выплата спонсора')
    elif event_type == 'SALARY_PAYMENT':
        salaries = (await session.execute(text("""
            SELECT COALESCE(cp.contract_salary,p.salary)
            FROM club_players cp JOIN players p ON p.id=cp.player_id
            WHERE cp.club_id=:c
        """), {'c': club_id})).scalars().all()
        amount = int(wage_bill(salaries))
        await record_financial_transaction(session, club_id, 'WAGES', -amount, 'Сезонная выплата зарплат')


async def recover_stale_processing_events(session, timeout_minutes=15):
    result = await session.execute(text("""
        UPDATE season_events
        SET status='FAILED',
            last_error=COALESCE(last_error, 'Event worker timeout: stale PROCESSING event'),
            processed_at=NULL
        WHERE status='PROCESSING'
          AND processing_at < now() - (:timeout_minutes * INTERVAL '1 minute')
        RETURNING id
    """), {'timeout_minutes': int(timeout_minutes)})
    return [int(row[0]) for row in result.all()]


async def process_due_events(session, now):
    if now is None:
        now = datetime.now(timezone.utc)
    await recover_stale_processing_events(session)
    rows = (await session.execute(text("""
        SELECT id,event_type,payload,attempts
        FROM season_events
        WHERE status='PENDING'
          AND scheduled_at<=CAST(:now AS timestamptz)
        ORDER BY scheduled_at,id
        FOR UPDATE SKIP LOCKED
        LIMIT 100
    """), {'now': now})).mappings().all()
    processed = 0
    for row in rows:
        event_id = int(row['id'])
        try:
            await session.execute(text("""
                UPDATE season_events
                SET status='PROCESSING', attempts=attempts+1, processing_at=now()
                WHERE id=:id AND status='PENDING'
            """), {'id': event_id})
            payload = dict(row['payload'] or {})
            event_type = str(row['event_type'])
            async with session.begin_nested():
                if event_type.startswith('MATCH_'):
                    await _process_match_event(session, event_type, payload)
                elif event_type in {'SEASON_START','SEASON_END','PROMOTION_RELEGATION','CONTRACT_WINDOW_OPEN','CONTRACT_WINDOW_CLOSE'}:
                    from app.game.season import handle_season_event
                    await handle_season_event(session, event_type, payload)
                else:
                    await _process_finance_event(session, event_type, payload)
            await session.execute(text("""
                UPDATE season_events
                SET status='DONE',processed_at=now(),processing_at=NULL,last_error=NULL
                WHERE id=:id AND status='PROCESSING'
            """), {'id': event_id})
            processed += 1
        except EventDeferred as exc:
            # Keep the event retryable. A round event must not be marked DONE or
            # permanently FAILED just because the previous round is still live.
            logger.warning('Season event deferred: id=%s type=%s reason=%s', event_id, row['event_type'], exc)
            await session.execute(text("""
                UPDATE season_events
                SET status='PENDING',processing_at=NULL,
                    scheduled_at=now()+INTERVAL '1 minute',last_error=:error
                WHERE id=:id
            """), {'id': event_id, 'error': str(exc)[:4000]})
        except Exception as exc:
            logger.exception('Season event failed: id=%s type=%s', row['id'], row['event_type'])
            await session.execute(text("""
                UPDATE season_events
                SET status='FAILED',processing_at=NULL,last_error=:error
                WHERE id=:id
            """), {'id': event_id, 'error': str(exc)[:4000]})
    return processed


async def retry_failed_events(session, limit=100):
    """Move failed events back to PENDING for an explicit admin retry."""
    result = await session.execute(text("""
        UPDATE season_events
        SET status='PENDING',last_error=NULL,processing_at=NULL,scheduled_at=LEAST(scheduled_at, now())
        WHERE id IN (
            SELECT id FROM season_events
            WHERE status='FAILED'
            ORDER BY id
            LIMIT :limit
            FOR UPDATE SKIP LOCKED
        )
        RETURNING id
    """), {'limit': int(limit)})
    return [int(row[0]) for row in result.all()]
