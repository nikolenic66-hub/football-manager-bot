import asyncio
import json
import logging
from sqlalchemy import text
from .db import SessionLocal
from .services import advance_live_matches, finalize_live_round, start_round_halftime, queue_notification, deliver_due_notifications

logger=logging.getLogger(__name__)
IMPORTANT_LIVE_EVENTS={'GOAL','ASSIST','SHOT','YELLOW','RED','INJURY','SUBSTITUTION'}
DANGEROUS_SHOT_RESULTS={'GOAL','SAVED','BLOCKED'}

async def start_due_rounds(s):
    rows=(await s.execute(text("""SELECT id,league_id,round
        FROM matches WHERE status='SCHEDULED' AND scheduled_at IS NOT NULL AND scheduled_at<=now()
        ORDER BY league_id,round,id FOR UPDATE SKIP LOCKED"""))).mappings().all()
    started=[]
    seen=set()
    for row in rows:
        key=(row['league_id'],row['round'])
        if key in seen: continue
        seen.add(key)
        try:
            data=await start_round_halftime(s,row['league_id'],row['round'])
            started.append((row['league_id'],row['round'],data))
        except ValueError:
            continue
    return started

async def queue_live_event_notifications(s, match_id):
    row=(await s.execute(text('''SELECT m.id,m.current_minute,m.home_score,m.away_score,
        m.home_halftime_score,m.away_halftime_score,m.live_event_cursor,
        m.home_club_id,m.away_club_id,h.name home,a.name away
        FROM matches m JOIN clubs h ON h.id=m.home_club_id JOIN clubs a ON a.id=m.away_club_id
        WHERE m.id=:m FOR UPDATE'''),{'m':match_id})).mappings().first()
    if not row: return
    events=(await s.execute(text('''SELECT id,minute,event_type,description,club_id,player_id,secondary_player_id,metadata
        FROM match_events WHERE match_id=:m AND id>:cursor AND minute<=:minute ORDER BY id'''),
        {'m':match_id,'cursor':row['live_event_cursor'],'minute':row['current_minute']})).mappings().all()
    if not events: return
    player_ids={x['secondary_player_id'] for x in events if x['secondary_player_id']}
    player_names={}
    if player_ids:
        prs=(await s.execute(text('SELECT id,first_name,last_name FROM players WHERE id=ANY(:ids)'),{'ids':list(player_ids)})).mappings().all()
        player_names={r['id']:f"{r['first_name']} {r['last_name']}" for r in prs}
    # Load the goal history once per worker tick; do not issue one SQL query per
    # Telegram event. This keeps live rounds cheap even with several matches.
    max_event_id=max(e['id'] for e in events)
    goal_rows=(await s.execute(text('''SELECT id,club_id FROM match_events
        WHERE match_id=:m AND event_type='GOAL' AND id<=:id AND minute<=:minute ORDER BY id'''),
        {'m':match_id,'id':max_event_id,'minute':row['current_minute']})).mappings().all()
    goal_prefix={}
    gh=ga=0; gi=0
    for e_goal in goal_rows:
        if e_goal['club_id']==row['home_club_id']: gh+=1
        elif e_goal['club_id']==row['away_club_id']: ga+=1
        goal_prefix[e_goal['id']]=(gh,ga)
    for e in events:
        typ=e['event_type']
        if typ not in IMPORTANT_LIVE_EVENTS:
            continue
        if e['minute']<=45:
            event_home_base=event_away_base=0
        else:
            event_home_base=int(row['home_halftime_score'] or 0)
            event_away_base=int(row['away_halftime_score'] or 0)
        # Use the complete goal history, not only the current cursor batch.
        prior_home,prior_away=goal_prefix.get(e['id'],(0,0))
        if e['minute']>45:
            prior_home=max(0,prior_home-int(row['home_halftime_score'] or 0))
            prior_away=max(0,prior_away-int(row['away_halftime_score'] or 0))
        score=f"{row['home']} <b>{event_home_base+prior_home}:{event_away_base+prior_away}</b> {row['away']}"
        if typ=='GOAL':
            assist=player_names.get(e['secondary_player_id'])
            body=f"{e['minute']}′ {e['description']}\\n{score}"
            if assist: body += f"\\n🅰️ Ассист: {assist}"
            for cid in (row['home_club_id'],row['away_club_id']):
                await _queue_for_club(s,cid,'MATCH','⚽ ГОЛ',body,f"live-event:{e['id']}")
        elif typ=='ASSIST':
            # Assist is folded into the goal notification to avoid duplicate Telegram messages.
            continue
        elif typ=='SHOT':
            meta=e['metadata'] or {}
            if isinstance(meta,str):
                try: meta=json.loads(meta)
                except Exception: meta={}
            if meta.get('result') not in DANGEROUS_SHOT_RESULTS:
                continue
            await _queue_for_club(s,e['club_id'],'MATCH','🔥 Опасный момент',f"{e['minute']}′ {e['description']}\\n{score}",f"live-event:{e['id']}")
        elif typ=='YELLOW':
            await _queue_for_club(s,e['club_id'],'DISCIPLINE','🟨 Жёлтая карточка',f"{e['minute']}′ {e['description']}",f"live-event:{e['id']}")
        elif typ=='INJURY':
            await _queue_for_club(s,e['club_id'],'INJURY','🩹 Травма',f"{e['minute']}′ {e['description']}",f"live-event:{e['id']}")
        elif typ=='RED':
            await _queue_for_club(s,e['club_id'],'DISCIPLINE','🟥 Красная карточка',f"{e['minute']}′ {e['description']}",f"live-event:{e['id']}")
        elif typ=='SUBSTITUTION':
            await _queue_for_club(s,e['club_id'],'MATCH','🔄 Замена',f"{e['minute']}′ {e['description']}",f"live-event:{e['id']}")
    await s.execute(text('UPDATE matches SET live_event_cursor=:id WHERE id=:m'),{'id':max(e['id'] for e in events),'m':match_id})

async def _queue_for_club(s, club_id, category, title, body, dedupe_key):
    uid=(await s.execute(text('SELECT owner_user_id FROM clubs WHERE id=:c'),{'c':club_id})).scalar_one_or_none()
    if uid is not None:
        await queue_notification(s,uid,category,title,body,dedupe_key)

async def live_worker(bot):
    while True:
        try:
            async with SessionLocal() as s:
                await start_due_rounds(s)
                transitions,finished=await advance_live_matches(s)
                live_ids=(await s.execute(text("SELECT id FROM matches WHERE status='LIVE' ORDER BY id"))).scalars().all()
                for mid in live_ids:
                    await queue_live_event_notifications(s,mid)
                if finished:
                    leagues=(await s.execute(text('SELECT DISTINCT league_id FROM matches WHERE id=ANY(:ids)'),{'ids':[r['match_id'] for r in finished]})).scalars().all()
                    for lid in leagues:
                        await finalize_live_round(s,lid,finished)
                await s.commit()
            async with SessionLocal() as delivery_session:
                await deliver_due_notifications(bot,delivery_session)
        except Exception:
            logger.exception('Live worker iteration failed')
        await asyncio.sleep(1)
