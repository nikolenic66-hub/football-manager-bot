import asyncio
import logging
from datetime import datetime, timezone
from sqlalchemy import text
from .db import SessionLocal, engine
from .services import advance_live_matches, finalize_live_round, start_round_halftime, queue_notification, deliver_due_notifications, display_player_name, cancel_stale_match_start_notifications, fill_waiting_rookie_leagues, enrich_pending_live_events, _force_finish_live_match
from app.game.season import check_season_complete
from .game.event_queue import process_due_events

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
          AND m.competition_type='LEAGUE'
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
        expected=int(row['current_round'] or 0)
        if int(row['round']) != expected:
            skipped += 1
            logger.info(
                'Skipping due match round league=%s round=%s: waiting for round %s',
                row['league_id'],row['round'],expected
            )
            continue
        if expected > 1:
            previous=(await s.execute(text("""SELECT COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE UPPER(COALESCE(status,'')) NOT IN ('FINISHED','FAILED')) AS pending
                FROM matches
                WHERE league_id=:l AND competition_type='LEAGUE' AND round=:r"""),
                {'l':int(row['league_id']),'r':expected-1})).mappings().one()
            if int(previous['total'] or 0)==0 or int(previous['pending'] or 0)>0:
                skipped += 1
                logger.warning(
                    'Skipping kickoff league=%s round=%s: previous round=%s is not terminal (total=%s pending=%s)',
                    row['league_id'],expected,expected-1,previous['total'],previous['pending']
                )
                continue
        try:
            async with s.begin_nested():
                data=await start_round_halftime(s,row['league_id'],expected)
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

async def finalize_stale_live_matches(s, age_hours=2):
    """Force-terminalize LIVE matches older than the configured wall-clock limit."""
    stale = (await s.execute(text("""SELECT id,league_id,competition_type,current_minute
        FROM matches
        WHERE status='LIVE'
          AND UPPER(COALESCE(competition_type,'LEAGUE')) IN ('LEAGUE','FRIENDLY')
          AND COALESCE(started_at,phase_started_at,scheduled_at) < now() - (:hours * INTERVAL '1 hour')
        ORDER BY id
        LIMIT 100 FOR UPDATE SKIP LOCKED"""), {'hours': int(age_hours)})).mappings().all()
    finished=[]
    for match in stale:
        match_id=int(match['id'])
        try:
            async with s.begin_nested():
                result=await _force_finish_live_match(s,match_id)
            if result:
                await s.execute(text("""UPDATE matches SET status='FINISHED',live_phase='FINISHED',
                    finished_at=COALESCE(finished_at,now()),halftime_locked=FALSE,forced_finish=TRUE
                    WHERE id=:m AND status='LIVE'"""), {'m':match_id})
                finished.append(result)
                logger.error('Force-finished stale LIVE match older than %sh: match_id=%s minute=%s',
                             age_hours,match_id,result.get('minute',match['current_minute']))
            else:
                await s.execute(text("""UPDATE matches SET status='FAILED',live_phase='FINISHED',
                    finished_at=COALESCE(finished_at,now()),halftime_locked=FALSE
                    WHERE id=:m AND status='LIVE'"""), {'m':match_id})
                logger.error('Marked unrecoverable stale LIVE match FAILED: match_id=%s',match_id)
        except Exception:
            logger.exception('Stale LIVE match force-finalization failed; marking FAILED: match_id=%s',match_id)
            await s.execute(text("""UPDATE matches SET status='FAILED',live_phase='FINISHED',
                finished_at=COALESCE(finished_at,now()),halftime_locked=FALSE
                WHERE id=:m AND status='LIVE'"""), {'m':match_id})
    return finished


async def queue_live_event_notifications(s, match_id):
    """Queue only important live events; routine events stay in match_events/live screen."""
    row=(await s.execute(text("""SELECT m.id,m.current_minute,m.home_score,m.away_score,
        m.home_halftime_score,m.away_halftime_score,m.live_event_cursor,
        m.home_club_id,m.away_club_id,h.name home,a.name away
        FROM matches m JOIN clubs h ON h.id=m.home_club_id JOIN clubs a ON a.id=m.away_club_id
        WHERE m.id=:m FOR UPDATE"""),{'m':match_id})).mappings().first()
    if not row: return 0
    events=(await s.execute(text("""SELECT e.id,e.minute,e.event_type,e.description,e.metadata,e.club_id,e.player_id,e.secondary_player_id
        FROM match_events e
        WHERE e.match_id=:m AND e.id>:cursor AND e.minute<=:minute
        ORDER BY e.minute,e.id LIMIT 20"""),
        {'m':match_id,'cursor':row['live_event_cursor'],'minute':row['current_minute']})).mappings().all()
    if not events: return 0

    # Routine events are intentionally NOT notifications. They remain available
    # in match_events and are rendered by the dedicated live screen.
    important=[e for e in events if e['event_type'] in {'GOAL','RED'}]
    important.extend(e for e in events if e['event_type']=='YELLOW')
    important.sort(key=lambda e:(int(e['minute']),int(e['id'])))
    player_ids={e['secondary_player_id'] for e in important if e['secondary_player_id']}
    player_ids.update(e['player_id'] for e in important if e['player_id'])
    player_names={}
    if player_ids:
        prs=(await s.execute(text('SELECT id,first_name,last_name FROM players WHERE id=ANY(:ids)'),{'ids':list(player_ids)})).mappings().all()
        player_names={r['id']:display_player_name(r['first_name'],r['last_name']) for r in prs}

    goal_rows=(await s.execute(text("""SELECT e.id,e.minute,e.club_id FROM match_events e
        WHERE e.match_id=:m AND e.event_type='GOAL' AND e.minute<=:minute
        ORDER BY e.minute,e.id"""),
        {'m':match_id,'minute':row['current_minute']})).mappings().all()
    goal_scores={}; gh=ga=0; gi=0
    for event in events:
        while gi<len(goal_rows):
            goal=goal_rows[gi]
            if (int(goal['minute']),int(goal['id'])) > (int(event['minute']),int(event['id'])): break
            if goal['club_id']==row['home_club_id']: gh+=1
            elif goal['club_id']==row['away_club_id']: ga+=1
            gi+=1
        goal_scores[event['id']]=(gh,ga)

    owner_rows=(await s.execute(text('''SELECT c.id,c.owner_user_id,u.telegram_id
        FROM clubs c JOIN users u ON u.id=c.owner_user_id
        WHERE c.id=ANY(:ids)'''), {'ids':[row['home_club_id'],row['away_club_id']]})).mappings().all()
    owner_cache={r['id']:(r['owner_user_id'],int(r['telegram_id'])) for r in owner_rows}
    for e in important:
        event_home,event_away=goal_scores.get(e['id'],(0,0))
        if e['minute']>45:
            event_home+=int(row['home_halftime_score'] or 0)
            event_away+=int(row['away_halftime_score'] or 0)
        score=f"{row['home']} <b>{event_home}:{event_away}</b> {row['away']}"
        description=str(e['description'] or '').replace(r'\n','\n')
        metadata=e.get('metadata') or {}
        if metadata.get('extra_time'):
            minute_label=str(metadata.get('display_minute') or e['minute'])
        else:
            minute_label=f"90+{int(e['minute'])-90}" if int(e['minute'])>90 else str(e['minute'])
        pname=player_names.get(e['player_id']) or 'Игрок'
        if e['event_type']=='GOAL':
            body=f"{minute_label}′ {pname}\n{score}"
            assist=player_names.get(e['secondary_player_id'])
            if assist: body += f"\n🅰️ Ассист: {assist}"
            await _queue_for_club(s,e['club_id'],'MATCH',owner_cache,'⚽ ГОЛ',body,f"live-event:{e['id']}")
        elif e['event_type']=='YELLOW':
            club_name=row['home'] if int(e['club_id'])==int(row['home_club_id']) else row['away']
            body=f"{minute_label}′ {pname} ({club_name}) получает жёлтую.\n{score}"
            await _queue_for_club(s,e['club_id'],'DISCIPLINE',owner_cache,'🟨 ЖЁЛТАЯ',body,f"live-event:{e['id']}")
        elif e['event_type']=='RED':
            body=f"{minute_label}′ {pname}\n{score}"
            await _queue_for_club(s,e['club_id'],'DISCIPLINE',owner_cache,'🟥 КРАСНАЯ',body,f"live-event:{e['id']}")
    await s.execute(text('UPDATE matches SET live_event_cursor=:id WHERE id=:m'),{'id':max(e['id'] for e in events),'m':match_id})
    return len(events)


async def _queue_for_club(s, club_id, category, owner_cache, title, body, dedupe_key):
    cached=owner_cache.get(club_id) if owner_cache is not None else None
    if cached is None:
        cached=(await s.execute(text('''SELECT c.owner_user_id,u.telegram_id FROM clubs c JOIN users u ON u.id=c.owner_user_id WHERE c.id=:c'''),{'c':club_id})).first()
    if not cached:
        return
    uid,telegram_id=cached
    if int(telegram_id) < 0:
        return
    await queue_notification(s,uid,category,title,body,dedupe_key)

async def _open_live_worker_lock():
    """Open a dedicated connection and acquire the session-scoped worker lock."""
    conn = await engine.connect()
    try:
        acquired = bool(
            (await conn.execute(
                text("SELECT pg_try_advisory_lock(hashtext('football-manager-live-worker'))")
            )).scalar_one()
        )
    except Exception:
        try:
            await conn.close()
        except Exception:
            logger.debug("Failed to close unusable live-worker lock connection", exc_info=True)
        raise
    if not acquired:
        await conn.close()
        return None
    return conn


async def live_worker(bot):
    # One process owns the live scheduler. The advisory lock is session-scoped,
    # so its dedicated connection must remain alive. Check it on each iteration:
    # pool_pre_ping does not validate a connection that is already checked out.
    lock_conn = await _open_live_worker_lock()
    if lock_conn is None:
        logger.warning('Another live worker is already active; this worker will stay idle.')
        return
    try:
        due_check_at=0.0
        rookie_check_at=0.0
        round_reconcile_at=0.0
        due_count=started_count=due_failed=0
        consecutive_failures=0
        last_failure_signature=None
        last_failure_log_at=0.0
        loop=asyncio.get_running_loop()
        while True:
            sleep_for=1.0
            try:
                # A long-lived checked-out connection can be dropped by the DB
                # or network even when pool_pre_ping is enabled. Reconnect and
                # reacquire the advisory lock before doing any scheduler work.
                try:
                    if lock_conn.closed or lock_conn.invalidated:
                        raise ConnectionError("live-worker lock connection is closed")
                    await lock_conn.execute(text("SELECT 1"))
                except Exception:
                    logger.warning("Live-worker lock connection was lost; reconnecting", exc_info=True)
                    try:
                        if not lock_conn.closed:
                            await lock_conn.close()
                    except Exception:
                        logger.debug("Failed to close lost live-worker lock connection", exc_info=True)
                    lock_conn = await _open_live_worker_lock()
                    if lock_conn is None:
                        logger.warning('Live-worker lock was acquired by another process; this worker will stop.')
                        return
                async with SessionLocal() as s:
                    # Serialize scheduler writes with the admin's destructive full reset.
                    # The lock is transaction-scoped; reacquire it after any explicit commit below.
                    await s.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('football_manager_full_reset', 0))"))
                    from app.game.fitness import recover_daily_fitness
                    recovered_players = await recover_daily_fitness(s)
                    if recovered_players:
                        logger.info('Daily fitness recovery applied: players=%s', recovered_players)
                    now_mono=loop.time()
                    if now_mono >= due_check_at:
                        due_count = await process_due_events(s, datetime.now(timezone.utc))
                        await s.commit()
                        # The commit releases the reset/scheduler mutex for the due-event phase.
                        # Reacquire before touching active match rows or season transitions.
                        await s.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('football_manager_full_reset', 0))"))
                        started_count = due_count
                        due_failed = 0
                        due_check_at=now_mono+1.0
                    if now_mono >= rookie_check_at:
                        filled_rookies, checked_rookies = await fill_waiting_rookie_leagues(s)
                        rookie_check_at=now_mono+3600.0
                        if checked_rookies:
                            logger.info('Rookie league fill check: leagues=%s bots_added=%s', checked_rookies, filled_rookies)
                    stale_finished=await finalize_stale_live_matches(s, age_hours=2)
                    transitions,finished=await advance_live_matches(s)
                    finished = stale_finished + finished
                    logger.info('Live worker iteration: due_matches=%s started_rounds=%s failed_rounds=%s live_transitions=%s finished=%s stale_finished=%s', due_count, started_count, due_failed, len(transitions), len(finished), len(stale_finished))
                    live_ids=(await s.execute(text("SELECT id FROM matches WHERE status='LIVE' AND UPPER(COALESCE(competition_type,'LEAGUE')) IN ('LEAGUE','FRIENDLY') ORDER BY id LIMIT 25"))).scalars().all()
                    for mid in live_ids:
                        await queue_live_event_notifications(s,mid)
                    if finished:
                        leagues=(await s.execute(text("SELECT DISTINCT league_id FROM matches WHERE id=ANY(:ids) AND competition_type='LEAGUE'"),{'ids':[r['match_id'] for r in finished]})).scalars().all()
                        for lid in leagues:
                            await finalize_live_round(s,lid,finished)
                    # Reconcile FAILED-only rounds and missing/fully-terminal rounds
                    # periodically. Without this path a round containing no FINISHED
                    # result would never call finalize_live_round at all.
                    if now_mono >= round_reconcile_at:
                        reconcile_ids=(await s.execute(text("""SELECT l.id
                            FROM leagues l
                            WHERE l.status='ACTIVE' AND (
                                EXISTS (SELECT 1 FROM matches f
                                    WHERE f.league_id=l.id AND f.competition_type='LEAGUE'
                                      AND f.round=l.current_round AND f.status='FAILED')
                                OR NOT EXISTS (SELECT 1 FROM matches m
                                    WHERE m.league_id=l.id AND m.competition_type='LEAGUE'
                                      AND m.round=l.current_round AND m.status NOT IN ('FINISHED','FAILED'))
                            )
                            ORDER BY l.id"""))).scalars().all()
                        for lid in reconcile_ids:
                            await finalize_live_round(s,int(lid),[])
                        round_reconcile_at=now_mono+60.0
                    await check_season_complete(s)
                    await s.commit()
                async with SessionLocal() as ai_session:
                    await ai_session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('football_manager_full_reset', 0))"))
                    await enrich_pending_live_events(ai_session)
                async with SessionLocal() as delivery_session:
                    await delivery_session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('football_manager_full_reset', 0))"))
                    await cancel_stale_match_start_notifications(delivery_session)
                    await delivery_session.commit()
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
        # A disconnected PostgreSQL session releases its session-level advisory
        # lock automatically. Never issue UNLOCK through a known-dead connection.
        try:
            if not lock_conn.closed and not lock_conn.invalidated:
                await lock_conn.execute(
                    text("SELECT pg_advisory_unlock(hashtext('football-manager-live-worker'))")
                )
                await lock_conn.commit()
        except Exception:
            logger.info(
                "Live-worker advisory lock could not be explicitly released; "
                "PostgreSQL releases it when the session closes.",
                exc_info=True,
            )
        finally:
            try:
                if not lock_conn.closed:
                    await lock_conn.close()
            except Exception:
                logger.debug("Failed to close live-worker lock connection", exc_info=True)

