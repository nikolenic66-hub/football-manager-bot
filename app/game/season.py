from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
import json
from secrets import randbelow, token_urlsafe
from typing import Iterable

from sqlalchemy import text

MSK = timezone(timedelta(hours=3))


class SeasonState(str, Enum):
    PRESEASON = 'PRESEASON'
    ACTIVE = 'ACTIVE'
    SEASON_END = 'SEASON_END'
    OFFSEASON = 'OFFSEASON'
    CONTRACT_WINDOW = 'CONTRACT_WINDOW'


_STATE_FLOW = {
    SeasonState.PRESEASON: SeasonState.ACTIVE,
    SeasonState.ACTIVE: SeasonState.SEASON_END,
    SeasonState.SEASON_END: SeasonState.OFFSEASON,
    SeasonState.OFFSEASON: SeasonState.CONTRACT_WINDOW,
    SeasonState.CONTRACT_WINDOW: SeasonState.PRESEASON,
}


def next_season_state(state: SeasonState | str) -> SeasonState:
    current = state if isinstance(state, SeasonState) else SeasonState(str(state).upper())
    return _STATE_FLOW[current]


def season_finished(match_statuses: Iterable[str]) -> bool:
    statuses = list(match_statuses)
    return bool(statuses) and all(str(status).upper() in {'FINISHED', 'FAILED'} for status in statuses)


def deterministic_match_seed(season_seed: int, match_id: int) -> int:
    return int(season_seed) + int(match_id)


def new_season_seed() -> int:
    return randbelow(1_000_000_000)


async def _current_season(session):
    return (await session.execute(text("""
        SELECT id,sequence_id,season_number,state,season_seed,started_at,ended_at,offseason_at,preseason_at,champion_club_id
        FROM seasons ORDER BY id DESC LIMIT 1 FOR UPDATE
    """))).mappings().first()


async def check_season_complete(session) -> bool:
    season = await _current_season(session)
    if not season:
        return False
    if season['state'] == SeasonState.PRESEASON.value:
        await session.execute(text("UPDATE seasons SET state='ACTIVE',started_at=COALESCE(started_at,CAST(:now AS timestamptz)) WHERE id=:s"),
                              {'s': int(season['id']), 'now': datetime.now(timezone.utc)})
        return False
    if season['state'] == SeasonState.SEASON_END.value:
        return await run_season_end(session, int(season['id']))
    if season['state'] == SeasonState.OFFSEASON.value:
        await session.execute(text("UPDATE seasons SET state='CONTRACT_WINDOW' WHERE id=:s"), {'s':int(season['id'])})
        return True
    if season['state'] == SeasonState.CONTRACT_WINDOW.value:
        return await advance_contract_window(session)
    if season['state'] != SeasonState.ACTIVE.value:
        return False
    total = int((await session.execute(text("SELECT COUNT(*) FROM matches WHERE season_id=:s AND competition_type='LEAGUE'"), {'s': int(season['id'])})).scalar_one())
    pending = int((await session.execute(text("SELECT COUNT(*) FROM matches WHERE season_id=:s AND competition_type='LEAGUE' AND status NOT IN ('FINISHED','FAILED')"), {'s': int(season['id'])})).scalar_one())
    if total == 0 or pending:
        return False
    # A season with a damaged/missing league round must not be closed merely
    # because every match row that still exists is terminal.
    active_leagues=int((await session.execute(text("""SELECT COUNT(*) FROM leagues l
        WHERE l.status='ACTIVE' AND (l.season_id=:s OR EXISTS (
            SELECT 1 FROM matches m WHERE m.league_id=l.id AND m.season_id=:s
        ))"""), {'s': int(season['id'])})).scalar_one())
    if active_leagues:
        return False
    return await run_season_end(session, int(season['id']))


async def _snapshot_league(session, league_id, season_id):
    rows = (await session.execute(text("""
        SELECT t.club_id,c.name club_name,t.played,t.wins,t.draws,t.losses,
               t.goals_for,t.goals_against,t.points
        FROM league_teams t JOIN clubs c ON c.id=t.club_id
        WHERE t.league_id=:l
        ORDER BY t.points DESC,(t.goals_for-t.goals_against) DESC,t.goals_for DESC,c.id
    """), {'l': league_id})).mappings().all()
    league = (await session.execute(text("SELECT name,season_number FROM leagues WHERE id=:l"), {'l': league_id})).mappings().first()
    if not league or not rows:
        return list(rows)
    season_meta = (await session.execute(text("SELECT sequence_id,season_number FROM seasons WHERE id=:s"), {'s': season_id})).mappings().first()
    snapshot_payload = [dict(r) for r in rows]
    await session.execute(text("""
        INSERT INTO season_league_snapshots(sequence_id,season_id,league_id,league_name,tier,season_number,standings)
        VALUES(:seq,:s,:l,:name,:tier,:n,CAST(:standings AS jsonb))
        ON CONFLICT(season_id,league_id) DO NOTHING
    """), {
        'seq': int(season_meta['sequence_id']), 's': season_id, 'l': league_id,
        'name': str(league['name']), 'tier': str(league.get('tier') or ''),
        'n': int(league['season_number'] or 1), 'standings': json.dumps(snapshot_payload, default=str, ensure_ascii=False),
    })
    await session.execute(text("""
        INSERT INTO season_history(league_id,season_id,season_number,champion_club_id,promoted_club_ids,relegated_club_ids)
        VALUES(:l,:s,:n,:champ,'[]'::jsonb,'[]'::jsonb)
        ON CONFLICT(league_id,season_number) DO UPDATE SET
            season_id=EXCLUDED.season_id, champion_club_id=EXCLUDED.champion_club_id
    """), {'l': league_id, 's': season_id, 'n': int(league['season_number'] or 1), 'champ': int(rows[0]['club_id'])})
    return list(rows)


_TIER_ORDER = ('ROOKIE', 'BRONZE', 'SILVER', 'GOLD', 'DIAMOND', 'ELITE', 'LEGEND')
_TIER_LABELS = {
    'ROOKIE': 'Новичок', 'BRONZE': 'Бронза', 'SILVER': 'Серебро',
    'GOLD': 'Золото', 'DIAMOND': 'Алмаз', 'ELITE': 'Элита', 'LEGEND': 'Легенда',
}


async def _create_tier_league(session, tier: str, season_id: int, season_number: int, creator_user_id: int) -> dict:
    """Create a regular 16-club league for a tier that has no destination league."""
    code = token_urlsafe(9).upper().replace('-', '').replace('_', '')[:12]
    label = _TIER_LABELS[tier]
    row = (await session.execute(text("""
        INSERT INTO leagues(
            name, invite_code, creator_user_id, max_teams, total_rounds, tier, status,
            season_id, season_number, season_state, match_interval_days, match_time, start_date
        ) VALUES(
            :name, :code, :creator, 16, 30, :tier, 'ACTIVE',
            :season_id, :season_number, 'SEASON_END', 1, '20:00', CURRENT_DATE
        ) RETURNING id, name, tier, season_number, season_id, max_teams
    """), {
        'name': f'FM27 {label} {code}', 'code': code, 'creator': creator_user_id,
        'tier': tier, 'season_id': season_id, 'season_number': season_number,
    })).mappings().one()
    return dict(row)


async def _apply_promotion_relegation(session, leagues, standings, season_id):
    """Move clubs one tier at a time while preserving league-table rows.

    Membership is moved by updating league_teams.league_id, which preserves the
    just-finished season's points/statistics until the next-season reset. Club,
    player, transfer, contract and match records are never deleted here.
    """
    if not leagues:
        return

    by_tier = {tier: [] for tier in _TIER_ORDER}
    for raw in leagues:
        league = dict(raw)
        tier = str(league.get('tier') or 'ROOKIE').upper()
        if tier in by_tier and not str(league.get('name') or '').startswith('FM27 Quick Test'):
            league['tier'] = tier
            by_tier[tier].append(league)

    first_league = next((dict(x) for x in leagues if x.get('creator_user_id')), dict(leagues[0]))
    creator_user_id = int(first_league.get('creator_user_id') or 1)
    season_number = int(first_league.get('season_number') or 1)

    async def league_count(league_id):
        return int((await session.execute(text(
            "SELECT COUNT(*) FROM league_teams WHERE league_id=:l"
        ), {'l': int(league_id)})).scalar_one())

    async def ensure_destination(tier, destinations):
        if destinations:
            return destinations
        created = await _create_tier_league(session, tier, season_id, season_number, creator_user_id)
        destinations.append(created)
        by_tier[tier].append(created)
        return destinations

    for idx in range(len(_TIER_ORDER) - 1):
        lower_tier, upper_tier = _TIER_ORDER[idx], _TIER_ORDER[idx + 1]
        lower_leagues, upper_leagues = by_tier[lower_tier], by_tier[upper_tier]
        if not lower_leagues:
            continue

        promotions = []  # (source league id, club id)
        relegations = []
        # Keep promotion and relegation groups disjoint. A league needs at
        # least eight ranked clubs to move four up and four down without a
        # club being selected for both transitions in the same season.
        for league in lower_leagues:
            rows = standings.get(int(league['id']), [])
            if len(rows) >= 8:
                promotions.extend((int(league['id']), int(r['club_id'])) for r in rows[:4])
        for league in upper_leagues:
            rows = standings.get(int(league['id']), [])
            if len(rows) >= 8:
                relegations.extend((int(league['id']), int(r['club_id'])) for r in rows[-4:])

        if not promotions and not relegations:
            continue
        if promotions:
            upper_leagues = await ensure_destination(upper_tier, upper_leagues)
        if relegations and not lower_leagues:
            lower_leagues = await ensure_destination(lower_tier, lower_leagues)

        # Project the post-departure capacity first. This lets relegated clubs
        # occupy the places freed by promotions and vice versa, without creating
        # unnecessary extra leagues.
        departures = {}
        for source_id, _ in promotions + relegations:
            departures[source_id] = departures.get(source_id, 0) + 1

        async def plan_assignments(moves, destinations, destination_tier):
            counts = {}
            for destination in destinations:
                lid = int(destination['id'])
                counts[lid] = max(0, await league_count(lid) - departures.get(lid, 0))
            assignments = []
            for source_id, club_id in moves:
                available = [d for d in destinations if counts.get(int(d['id']), 0) < int(d.get('max_teams') or 16)]
                if not available:
                    created = await _create_tier_league(session, destination_tier, season_id,
                                                         season_number, creator_user_id)
                    destinations.append(created)
                    by_tier[destination_tier].append(created)
                    counts[int(created['id'])] = 0
                    available = [created]
                target = min(available, key=lambda d: (counts[int(d['id'])], int(d['id'])))
                assignments.append((source_id, club_id, int(target['id'])))
                counts[int(target['id'])] += 1
            return assignments

        promotion_assignments = await plan_assignments(promotions, upper_leagues, upper_tier) if promotions else []
        relegation_assignments = await plan_assignments(relegations, lower_leagues, lower_tier) if relegations else []

        # Move rows rather than deleting/reinserting them: played, points and goal
        # statistics remain intact until start_new_season resets the new table.
        for source_id, club_id, target_id in relegation_assignments + promotion_assignments:
            await session.execute(text("""
                UPDATE league_teams SET league_id=:target
                WHERE league_id=:source AND club_id=:club
            """), {'target': target_id, 'source': source_id, 'club': club_id})

        promoted_by_source = {}
        relegated_by_source = {}
        for source_id, club_id in promotions:
            promoted_by_source.setdefault(source_id, []).append(club_id)
        for source_id, club_id in relegations:
            relegated_by_source.setdefault(source_id, []).append(club_id)
        for league in lower_leagues + upper_leagues:
            source_id = int(league['id'])
            await session.execute(text("""
                UPDATE season_history
                SET promoted_club_ids=CAST(:p AS jsonb), relegated_club_ids=CAST(:r AS jsonb)
                WHERE league_id=:l AND season_id=:s
            """), {'p': json.dumps(promoted_by_source.get(source_id, [])),
                  'r': json.dumps(relegated_by_source.get(source_id, [])), 'l': source_id, 's': season_id})


async def _apply_player_settlement(session, season_id):
    await session.execute(text("""
        INSERT INTO player_season_state(player_id,season_id,aged,development_applied,contract_processed,reward_claimed)
        SELECT cp.player_id,:s,FALSE,FALSE,FALSE,FALSE FROM club_players cp
        ON CONFLICT(player_id,season_id) DO NOTHING
    """), {'s': season_id})
    await session.execute(text("""
        UPDATE players p SET age=LEAST(40,p.age+1)
        WHERE EXISTS (SELECT 1 FROM player_season_state ps WHERE ps.player_id=p.id AND ps.season_id=:s AND ps.aged=FALSE)
    """), {'s': season_id})
    await session.execute(text("UPDATE player_season_state SET aged=TRUE,development_applied=TRUE WHERE season_id=:s AND aged=FALSE"), {'s': season_id})
    await session.execute(text("""
        UPDATE club_players cp SET
            contract_until=GREATEST(COALESCE(cp.contract_until,CURRENT_DATE),CURRENT_DATE)+INTERVAL '1 year',
            contract_salary=COALESCE(cp.contract_salary,(SELECT p.salary FROM players p WHERE p.id=cp.player_id))
        WHERE cp.contract_until IS NULL OR cp.contract_until<=CURRENT_DATE+INTERVAL '180 days'
          AND EXISTS (SELECT 1 FROM player_season_state ps WHERE ps.player_id=cp.player_id AND ps.season_id=:s AND ps.contract_processed=FALSE)
    """), {'s': season_id})
    await session.execute(text("UPDATE player_season_state SET contract_processed=TRUE WHERE season_id=:s AND contract_processed=FALSE"), {'s': season_id})


async def _settle_rewards(session, leagues, season_id):
    from app.services import settle_season_prizes, apply_season_reputation
    for league in leagues:
        lid = int(league['id'])
        await apply_season_reputation(session, lid)
        await settle_season_prizes(session, lid)
    # Prize settlement is guarded by the financial transaction marker; this flag
    # records that the player-side reward checkpoint has also been completed.
    await session.execute(text("UPDATE player_season_state SET reward_claimed=TRUE WHERE season_id=:s AND reward_claimed=FALSE"), {'s': season_id})


async def run_season_end(session, season_id: int | None = None) -> bool:
    season = (await session.execute(text("SELECT id,season_number,state FROM seasons WHERE id=COALESCE(:s,(SELECT id FROM seasons ORDER BY id DESC LIMIT 1)) FOR UPDATE"), {'s': season_id})).mappings().first()
    if not season or season['state'] not in (SeasonState.ACTIVE.value, SeasonState.SEASON_END.value):
        return False
    season_id = int(season['id'])
    if season['state'] == SeasonState.ACTIVE.value:
        await session.execute(text("UPDATE seasons SET state='SEASON_END',ended_at=COALESCE(ended_at,CAST(:now AS timestamptz)) WHERE id=:s"), {'s': season_id, 'now': datetime.now(timezone.utc)})
    leagues = (await session.execute(text("""
        SELECT id,name,tier,season_number,season_id,creator_user_id,max_teams FROM leagues
        WHERE season_id=:s AND name NOT LIKE 'FM27 Quick Test%' ORDER BY tier,id FOR UPDATE
    """), {'s': season_id})).mappings().all()
    standings = {int(l['id']): await _snapshot_league(session, int(l['id']), season_id) for l in leagues}
    await _settle_rewards(session, leagues, season_id)
    await _apply_promotion_relegation(session, leagues, standings, season_id)
    await _apply_player_settlement(session, season_id)
    champion = None
    for tier in ('LEGEND','ELITE','DIAMOND','GOLD','SILVER','BRONZE','ROOKIE'):
        top = next((standings[int(l['id'])][0] for l in leagues if str(l['tier'] or '').upper() == tier and standings.get(int(l['id']))), None)
        if top:
            champion = int(top['club_id']); break
    now = datetime.now(timezone.utc)
    await session.execute(text("UPDATE seasons SET champion_club_id=:c,state='OFFSEASON',offseason_at=COALESCE(offseason_at,CAST(:now AS timestamptz)),preseason_at=COALESCE(preseason_at,CAST(:until AS timestamptz)) WHERE id=:s"),
                          {'c': champion, 's': season_id, 'now': now, 'until': now + timedelta(days=3)})
    from app.game.event_queue import queue_event
    await queue_event(session, 'CONTRACT_WINDOW_OPEN', now, {'season_id': season_id})
    return True


async def advance_contract_window(session, force=False):
    season = (await session.execute(text("SELECT id,state,preseason_at FROM seasons ORDER BY id DESC LIMIT 1 FOR UPDATE"))).mappings().first()
    if not season or season['state'] != SeasonState.CONTRACT_WINDOW.value:
        return False
    if not force and season['preseason_at'] is not None and season['preseason_at'] > datetime.now(timezone.utc):
        return False
    await start_new_season(session, int(season['id']))
    return True




async def ensure_initial_season(session) -> int:
    """Create the first PRESEASON record when a fresh/reset database has none."""
    existing = (await session.execute(text("SELECT id FROM seasons ORDER BY id DESC LIMIT 1"))).scalar_one_or_none()
    if existing is not None:
        return int(existing)
    seed = new_season_seed()
    sequence_id = int((await session.execute(text("""
        INSERT INTO season_sequences(root_seed, current_season_id)
        VALUES(:seed, NULL) RETURNING id
    """), {"seed": seed})).scalar_one())
    season_id = int((await session.execute(text("""
        INSERT INTO seasons(sequence_id, season_number, state, season_seed, started_at)
        VALUES(:seq, 1, 'PRESEASON', :seed, CAST(:now AS timestamptz)) RETURNING id
    """), {"seq": sequence_id, "seed": seed, "now": datetime.now(timezone.utc)})).scalar_one())
    await session.execute(text("UPDATE season_sequences SET current_season_id=:s WHERE id=:seq"),
                          {"s": season_id, "seq": sequence_id})
    await session.execute(text("""
        UPDATE leagues SET season_id=:s, season_number=1, season_state='PRESEASON'
        WHERE season_id IS NULL
    """), {"s": season_id})
    return season_id


async def start_new_season(session, previous_season_id: int | None = None) -> int:
    """Create a season and all fixtures atomically within the caller transaction."""
    async with session.begin_nested():
        return await _start_new_season_in_transaction(session, previous_season_id)


async def _start_new_season_in_transaction(session, previous_season_id: int | None = None) -> int:
    previous = None
    if previous_season_id is not None:
        previous = (await session.execute(text("SELECT id,sequence_id,season_number FROM seasons WHERE id=:s"), {'s': previous_season_id})).mappings().first()
    else:
        previous = (await session.execute(text("SELECT id,sequence_id,season_number FROM seasons ORDER BY id DESC LIMIT 1"))).mappings().first()
    if not previous:
        raise ValueError('Cannot start a new season without an existing season sequence')
    previous_season_id = int(previous['id'])
    sequence_id = int(previous['sequence_id'])
    next_number = int(previous['season_number']) + 1
    now = datetime.now(MSK)
    seed = new_season_seed()
    new_id = int((await session.execute(text("""
        INSERT INTO seasons(sequence_id,season_number,state,season_seed,started_at)
        VALUES(:seq,:n,'PRESEASON',:seed,CAST(:started AS timestamptz)) RETURNING id
    """), {'seq': sequence_id, 'n': next_number, 'seed': seed, 'started': now})).scalar_one())
    old_league_ids=[int(x) for x in (await session.execute(text(
        "SELECT id FROM leagues WHERE season_id=:old OR (season_id IS NULL AND season_number=:n) ORDER BY id FOR UPDATE"
    ), {'old': previous_season_id, 'n': int(previous['season_number'])})).scalars().all()]
    # Keep cup and Champions League records as immutable season history.
    # Generic match rows are retained too, so old calendars/results remain queryable.
    await session.execute(text("""UPDATE cups SET status='FINISHED'
        WHERE (season_id=:old OR season=:n) AND status IN ('WAITING','ACTIVE')"""),
        {'old': previous_season_id, 'n': int(previous['season_number'])})
    await session.execute(text("""UPDATE champions_league SET status='FINISHED'
        WHERE (season_id=:old OR season=:n) AND status IN ('WAITING','ACTIVE')"""),
        {'old': previous_season_id, 'n': int(previous['season_number'])})
    # Remove queued events that reference old fixtures before those fixtures are deleted.
    await session.execute(text("DELETE FROM season_events WHERE season_id=:old"), {'old': previous_season_id})
    # Keep previous-season matches as permanent history. Migration 071 scopes
    # fixture uniqueness by season_id, so the same clubs can meet again next year.
    await session.execute(text("DELETE FROM club_season_finances WHERE league_id=ANY(:leagues)"),
                          {'leagues': old_league_ids or [-1]})
    if old_league_ids:
        await session.execute(text("""
            UPDATE leagues SET season_id=:new,season_number=:n,season_state='PRESEASON',status='ACTIVE',current_round=1,total_rounds=30,finished_at=NULL
            WHERE id=ANY(:league_ids)
        """), {'new': new_id, 'n': next_number, 'league_ids': old_league_ids})
    from app.game.league import round_robin
    from app.game.calendar import get_next_free_date
    from app.game.event_queue import queue_event
    # Use a stable evening kickoff instead of inheriting the arbitrary hour/minute
    # at which an administrator happened to start the season.
    schedule_start = now.replace(hour=20, minute=0, second=0, microsecond=0)
    if schedule_start <= now:
        schedule_start += timedelta(days=1)
    for league in (await session.execute(text("SELECT id FROM leagues WHERE season_id=:s AND name NOT LIKE 'FM27 Quick Test%' ORDER BY id"), {'s': new_id})).mappings().all():
        lid = int(league['id'])
        clubs = [int(x) for x in (await session.execute(text("SELECT club_id FROM league_teams WHERE league_id=:l ORDER BY club_id"), {'l': lid})).scalars().all()]
        if len(clubs) < 16:
            from app.services import create_test_bot_club
            while len(clubs) < 16:
                bot_id, _ = await create_test_bot_club(session)
                await session.execute(text("INSERT INTO league_teams(league_id,club_id) VALUES(:l,:c) ON CONFLICT DO NOTHING"), {'l': lid, 'c': bot_id})
                clubs.append(int(bot_id))
        clubs = clubs[:16]
        await session.execute(text("DELETE FROM matches WHERE league_id=:l AND season_id=:s"), {'l': lid, 's': new_id})
        await session.execute(text("UPDATE league_teams SET played=0,wins=0,draws=0,losses=0,goals_for=0,goals_against=0,points=0 WHERE league_id=:l"), {'l': lid})
        for fixture in round_robin(clubs, True):
            kickoff = schedule_start + timedelta(days=(fixture.round_no - 1) * 2)
            kickoff = await get_next_free_date(session, kickoff, (fixture.home_club_id, fixture.away_club_id), priority=0)
            if not isinstance(kickoff, datetime):
                kickoff = datetime.combine(kickoff, time(20, 0), tzinfo=now.tzinfo)
            match_id = int((await session.execute(text("""
                INSERT INTO matches(season_id,league_id,round,home_club_id,away_club_id,seed,competition_type,scheduled_at)
                VALUES(:s,:l,:r,:h,:a,0,'LEAGUE',CAST(:k AS timestamptz)) RETURNING id
            """), {'s': new_id, 'l': lid, 'r': fixture.round_no, 'h': fixture.home_club_id, 'a': fixture.away_club_id, 'k': kickoff})).scalar_one())
            await session.execute(text("UPDATE matches SET seed=:seed WHERE id=:m"), {'seed': deterministic_match_seed(seed, match_id), 'm': match_id})
            await queue_event(session, 'MATCH_LEAGUE', kickoff, {'season_id': new_id, 'league_id': lid, 'match_id': match_id})
        await session.execute(text("UPDATE leagues SET season_state='ACTIVE',started_at=CAST(:started AS timestamptz) WHERE id=:l"), {'started': now, 'l': lid})
    # League-only season: cup and Champions League records remain as history, but no new tournaments are created.
    await session.execute(text("UPDATE seasons SET state='ACTIVE' WHERE id=:s"), {'s': new_id})
    await session.execute(text("UPDATE season_sequences SET current_season_id=:s WHERE id=:seq"), {'s': new_id, 'seq': sequence_id})
    return new_id


async def handle_season_event(session, event_type, payload):
    if event_type == 'CONTRACT_WINDOW_OPEN':
        await session.execute(text("UPDATE seasons SET state='CONTRACT_WINDOW' WHERE id=:s AND state='OFFSEASON'"), {'s':int(payload['season_id'])})
    elif event_type == 'CONTRACT_WINDOW_CLOSE':
        await advance_contract_window(session, force=True)
    elif event_type == 'SEASON_END':
        await run_season_end(session, int(payload.get('season_id')) if payload.get('season_id') else None)
    elif event_type == 'SEASON_START':
        await advance_contract_window(session, force=True)


async def advance_finished_leagues(session) -> list[int]:
    return [await start_new_season(session)]
