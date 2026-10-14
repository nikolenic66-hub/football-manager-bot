import asyncio
import json
import logging
from sqlalchemy import text
from .db import SessionLocal, engine
from .services import advance_live_matches, finalize_live_round, start_round_halftime, queue_notification, deliver_due_notifications, display_player_name

logger=logging.getLogger(__name__)
IMPORTANT_LIVE_EVENTS={'GOAL','ASSIST','SHOT','YELLOW','RED','INJURY','SUBSTITUTION'}
DANGEROUS_SHOT_RESULTS={'GOAL','SAVED','BLOCKED'}

async def start_due_rounds(s):
    # `leagues.current_round` is the next round that is allowed to start.
    # Later rounds may already be overdue in the calendar, but they must wait
    # until the current round is finished and finalize_live_round advances it.
    rows=(await s.execute(text("""SELECT m.id,m.league_id,m.round,l.current_round
        FROM matches m
        JOIN leagues l ON l.id=m.league_id
        WHERE m.status='SCHEDULED'
          AND m.scheduled_at IS NOT NULL
          AND m.scheduled_at<=now()
          AND l.status='ACTIVE'
        ORDER BY m.league_id,m.round,m.id
        FOR UPDATE OF m SKIP LOCKED"""))).mappings().all()
    started=[]
    failed=0
    skipped=0
    seen=set()
    for row in rows:
        key=(row['league_id'],row['round'])
        if key in seen:
            continue
        seen.add(key)
        expected=row['current_round']
        if row['round'] != expected:
            skipped += 1
            logger.info(
                'Skipping due match round league=%s round=%s: waiting for round %s',
                row['league_id'],row['round'],expected
            )
            continue
        try:
            async with s.begin_nested():
                data=await start_round_halftime(s,row['league_id'],row['round'])
            started.append((row['league_id'],row['round'],data))
        except ValueError as exc:
            failed += 1
            logger.warning('Could not start due match round league=%s round=%s: %s', row['league_id'], row['round'], exc)
        except Exception:
            failed += 1
            logger.exception('Failed to start due match round league=%s round=%s', row['league_id'], row['round'])
    logger.info(
        'Live kickoff check: due_matches=%s started_rounds=%s failed_rounds=%s skipped_rounds=%s',
        len(rows),len(started),failed,skipped
    )
    return started,len(started),len(rows),failed

async def queue_live_event_notifications(s, match_id):
    # Every cursor and every event query is scoped to this exact match.
    row=(await s.execute(text("""SELECT m.id,m.current_minute,m.home_score,m.away_score,
        m.home_halftime_score,m.away_halftime_score,m.live_event_cursor,
        m.home_club_id,m.away_club_id,h.name home,a.name away
        FROM matches m JOIN clubs h ON h.id=m.home_club_id JOIN clubs a ON a.id=m.away_club_id
        WHERE m.id=:m FOR UPDATE"""),{'m':match_id})).mappings().first()
    if not row: return 0
    events=(await s.execute(text("""SELECT e.id,e.minute,e.event_type,e.description,e.club_id,e.player_id,e.secondary_player_id,e.metadata
        FROM match_events e
        WHERE e.match_id=:m AND e.id>:cursor AND e.minute<=:minute
        ORDER BY e.minute,e.id LIMIT 2"""),
        {'m':match_id,'cursor':row['live_event_cursor'],'minute':row['current_minute']})).mappings().all()
    if not events: return 0

    player_ids={x['secondary_player_id'] for x in events if x['secondary_player_id']}
    player_names={}
    if player_ids:
        prs=(await s.execute(text('SELECT id,first_name,last_name FROM players WHERE id=ANY(:ids)'),{'ids':list(player_ids)})).mappings().all()
        player_names={r['id']:display_player_name(r['first_name'],r['last_name']) for r in prs}

    # Calculate the score at each event's own (minute,id) position, never from
    # the match's current score. Thus a 24' shot after an 18' goal shows 0:1.
    goal_rows=(await s.execute(text("""SELECT e.id,e.minute,e.club_id FROM match_events e
        WHERE e.match_id=:m AND e.event_type='GOAL' AND e.minute<=:minute
        ORDER BY e.minute,e.id"""),
        {'m':match_id,'minute':row['current_minute']})).mappings().all()
    goal_scores={}
    gh=ga=0; gi=0
    for event in events:
        while gi<len(goal_rows):
            goal=goal_rows[gi]
            if (int(goal['minute']),int(goal['id'])) > (int(event['minute']),int(event['id'])):
                break
            if goal['club_id']==row['home_club_id']: gh+=1
            elif goal['club_id']==row['away_club_id']: ga+=1
            gi+=1
        goal_scores[event['id']]=(gh,ga)

    owner_rows=(await s.execute(text('SELECT id,owner_user_id FROM clubs WHERE id=ANY(:ids)'), {'ids':[row['home_club_id'],row['away_club_id']]})).mappings().all()
    owner_cache={r['id']:r['owner_user_id'] for r in owner_rows}
    for e in events:
        typ=e['event_type']
        if typ not in IMPORTANT_LIVE_EVENTS:
            continue
        event_home,event_away=goal_scores.get(e['id'],(0,0))
        if e['minute']>45:
            event_home+=int(row['home_halftime_score'] or 0)
            event_away+=int(row['away_halftime_score'] or 0)
        score=f"{row['home']} <b>{event_home}:{event_away}</b> {row['away']}"
        description=str(e['description'] or '').replace(r'\n','\n')
        if typ=='GOAL':
            assist=player_names.get(e['secondary_player_id'])
            body=f"{e['minute']}′ {description}\n{score}"
            if assist: body += f"\n🅰️ Ассист: {assist}"
            for cid in (row['home_club_id'],row['away_club_id']):
                await _queue_for_club(s,cid,'MATCH',owner_cache,'⚽ ГОЛ',body,f"live-event:{e['id']}")
        elif typ=='ASSIST':
            continue
        elif typ=='SHOT':
            meta=e['metadata'] or {}
            if isinstance(meta,str):
                try: meta=json.loads(meta)
                except Exception: meta={}
            if meta.get('result') not in DANGEROUS_SHOT_RESULTS:
                continue
            await _queue_for_club(s,e['club_id'],'MATCH',owner_cache,'🔥 Опасный момент',f"{e['minute']}′ {description}\n{score}",f"live-event:{e['id']}")
        elif typ=='YELLOW':
            await _queue_for_club(s,e['club_id'],'DISCIPLINE',owner_cache,'🟨 Жёлтая карточка',f"{e['minute']}′ {description}",f"live-event:{e['id']}")
        elif typ=='INJURY':
            await _queue_for_club(s,e['club_id'],'INJURY',owner_cache,'🩹 Травма',f"{e['minute']}′ {description}",f"live-event:{e['id']}")
        elif typ=='RED':
            await _queue_for_club(s,e['club_id'],'DISCIPLINE',owner_cache,'🟥 Красная карточка',f"{e['minute']}′ {description}",f"live-event:{e['id']}")
        elif typ=='SUBSTITUTION':
            await _queue_for_club(s,e['club_id'],'MATCH',owner_cache,'🔄 Замена',f"{e['minute']}′ {description}",f"live-event:{e['id']}")
    await s.execute(text('UPDATE matches SET live_event_cursor=:id WHERE id=:m'),{'id':max(e['id'] for e in events),'m':match_id})
    return len(events)


async def _queue_for_club(s, club_id, category, owner_cache, title, body, dedupe_key):
    uid=owner_cache.get(club_id) if owner_cache is not None else (await s.execute(text('SELECT owner_user_id FROM clubs WHERE id=:c'),{'c':club_id})).scalar_one_or_none()
    if uid is not None:
        await queue_notification(s,uid,category,title,body,dedupe_key)

async def live_worker(bot):
    # One process owns the live scheduler. Row locks remain the second line of
    # defence for individual state transitions. The advisory lock is session
    # scoped, so keep the connection open for the lifetime of this worker.
    lock_conn = await engine.connect()
    try:
        acquired = bool((await lock_conn.execute(text("SELECT pg_try_advisory_lock(hashtext('football-manager-live-worker'))"))).scalar_one())
        if not acquired:
            logger.warning('Another live worker is already active; this worker will stay idle.')
            await lock_conn.close()
            return
        due_check_at=0.0
        due_count=started_count=due_failed=0
        consecutive_failures=0
        last_failure_signature=None
        last_failure_log_at=0.0
        loop=asyncio.get_running_loop()
        while True:
            sleep_for=1.0
            try:
                async with SessionLocal() as s:
                    now_mono=loop.time()
                    if now_mono >= due_check_at:
                        _, started_count, due_count, due_failed = await start_due_rounds(s)
                        due_check_at=now_mono+30.0
                    transitions,finished=await advance_live_matches(s)
                    logger.info('Live worker iteration: due_matches=%s started_rounds=%s failed_rounds=%s live_transitions=%s finished=%s', due_count, started_count, due_failed, len(transitions), len(finished))
                    live_ids=(await s.execute(text("SELECT id FROM matches WHERE status='LIVE' ORDER BY id LIMIT 25"))).scalars().all()
                    for mid in live_ids:
                        await queue_live_event_notifications(s,mid)
                    if finished:
                        leagues=(await s.execute(text('SELECT DISTINCT league_id FROM matches WHERE id=ANY(:ids)'),{'ids':[r['match_id'] for r in finished]})).scalars().all()
                        for lid in leagues:
                            await finalize_live_round(s,lid,finished)
                    await s.commit()
                async with SessionLocal() as delivery_session:
                    await deliver_due_notifications(bot,delivery_session,limit=20)
                if consecutive_failures:
                    logger.info('Live worker recovered after %s consecutive failures.', consecutive_failures)
                consecutive_failures=0
                last_failure_signature=None
                last_failure_log_at=0.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                signature=f'{type(exc).__name__}:{exc}'
                if signature == last_failure_signature:
                    consecutive_failures += 1
                else:
                    consecutive_failures=1
                    last_failure_signature=signature
                    last_failure_log_at=0.0
                now_mono=loop.time()
                if consecutive_failures < 3:
                    if consecutive_failures == 1:
                        logger.exception('Live worker iteration failed')
                    else:
                        logger.warning('Live worker iteration failed (%s/%s): %s', consecutive_failures, 3, exc)
                    sleep_for=5.0
                else:
                    sleep_for=60.0 if consecutive_failures == 3 else 120.0
                    if last_failure_log_at == 0.0 or now_mono-last_failure_log_at >= 60.0:
                        logger.exception('Live worker iteration failed repeatedly (consecutive_failures=%s): %s', consecutive_failures, exc)
                        last_failure_log_at=now_mono
                    else:
                        logger.warning('Live worker backoff active: consecutive_failures=%s sleep=%ss', consecutive_failures, int(sleep_for))
            await asyncio.sleep(sleep_for)
    finally:
        try:
            await lock_conn.execute(text("SELECT pg_advisory_unlock(hashtext('football-manager-live-worker'))"))
        except Exception:
            pass
        await lock_conn.close()

