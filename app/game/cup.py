from datetime import datetime, time, timedelta, timezone
from types import SimpleNamespace
from collections import defaultdict

from sqlalchemy import text

from app.game.ratings import player_rating
from app.services import display_player_name, create_test_bot_club
from app.game.event_queue import queue_event
from app.game.calendar import get_next_free_date
from app.game.rng import rng as make_rng

ROUND_LABELS = {1: '1/32 финала', 2: '1/16 финала', 3: '1/8 финала', 4: '1/4 финала', 5: '1/2 финала', 6: 'Финал'}


def cup_size(team_count):
    return 64


def _seed_for(cup_id, round_no, home_id, away_id):
    return int(cup_id) * 1_000_003 + int(round_no) * 10_009 + int(home_id) * 1_009 + int(away_id)


async def _resolve_cup_season(session, season_id=None):
    if season_id is not None:
        row = (await session.execute(text("SELECT id,season_number,season_seed FROM seasons WHERE id=:id"),
                                     {'id': int(season_id)})).mappings().first()
        if row:
            return dict(row)
        row = (await session.execute(text("SELECT id,season_number,season_seed FROM seasons WHERE season_number=:n ORDER BY id DESC LIMIT 1"),
                                     {'n': int(season_id)})).mappings().first()
        if row:
            return dict(row)
        raise ValueError(f'Сезон с ID/номером {season_id} не найден.')
    row = (await session.execute(text("""SELECT id,season_number,season_seed FROM seasons
        WHERE state IN ('ACTIVE','PRESEASON') ORDER BY CASE state WHEN 'ACTIVE' THEN 0 ELSE 1 END,id DESC LIMIT 1"""))).mappings().first()
    if not row:
        raise ValueError('Нет текущего сезона для запуска Кубка.')
    return dict(row)


TIER_STRENGTH = {'LEGEND': 7, 'ELITE': 6, 'DIAMOND': 5, 'GOLD': 4, 'SILVER': 3, 'BRONZE': 2, 'ROOKIE': 1}


async def _cup_candidates(session):
    rows = (await session.execute(text("""
        WITH ranked AS (
            SELECT lt.club_id,lt.league_id,l.name AS league_name,l.tier,
                   COALESCE(lt.points,0) AS points,COALESCE(lt.played,0) AS played,
                   COALESCE(lt.goals_for,0) AS goals_for,COALESCE(lt.goals_against,0) AS goals_against,
                   COALESCE(c.reputation,0) AS reputation,
                   ROW_NUMBER() OVER (PARTITION BY lt.league_id ORDER BY
                     COALESCE(lt.points,0) DESC,
                     COALESCE(lt.goals_for,0)-COALESCE(lt.goals_against,0) DESC,
                     COALESCE(lt.goals_for,0) DESC,COALESCE(c.reputation,0) DESC,lt.club_id) AS position
            FROM league_teams lt JOIN leagues l ON l.id=lt.league_id JOIN clubs c ON c.id=lt.club_id
            WHERE l.status='ACTIVE'
        )
        SELECT * FROM ranked
        ORDER BY CASE UPPER(COALESCE(tier,'ROOKIE'))
            WHEN 'LEGEND' THEN 7 WHEN 'ELITE' THEN 6 WHEN 'DIAMOND' THEN 5
            WHEN 'GOLD' THEN 4 WHEN 'SILVER' THEN 3 WHEN 'BRONZE' THEN 2 ELSE 1 END DESC,
            position ASC,points DESC,goals_for-goals_against DESC,reputation DESC,league_id,club_id
    """))).mappings().all()
    by_league = defaultdict(list)
    for row in rows:
        by_league[int(row['league_id'])].append(dict(row))
    ordered_leagues = sorted(by_league, key=lambda lid: (
        -TIER_STRENGTH.get(str(by_league[lid][0].get('tier') or 'ROOKIE').upper(), 1), lid))
    selected, seen = [], set()
    # Take one club per league in each pass before taking second/third/fourth
    # clubs. This keeps the national Cup genuinely cross-league.
    max_depth = max((len(by_league[lid]) for lid in ordered_leagues), default=0)
    for depth in range(max_depth):
        for league_id in ordered_leagues:
            if depth >= len(by_league[league_id]):
                continue
            row = by_league[league_id][depth]
            club_id = int(row['club_id'])
            if club_id in seen:
                continue
            seen.add(club_id)
            item = dict(row)
            item['seed_rating'] = (TIER_STRENGTH.get(str(item.get('tier') or 'ROOKIE').upper(), 1) * 10000
                                   + int(item.get('points') or 0) * 100
                                   + int(item.get('goals_for') or 0) - int(item.get('goals_against') or 0)
                                   + int(item.get('reputation') or 0))
            selected.append(item)
            if len(selected) == 64:
                return selected
    return selected


async def _insert_match(session, cup_id, host_league_id, round_no, home_id, away_id, scheduled_at, season_id=None):
    match_id = int((await session.execute(text("""INSERT INTO matches(season_id,league_id,round,home_club_id,away_club_id,seed,competition_type,
                        started_at,scheduled_at,prestart_notified_at)
        VALUES(:season,:l,:r,:h,:a,0,'CUP',NULL,CAST(:kickoff AS timestamptz),NULL) RETURNING id"""), {
        'season': season_id, 'l': int(host_league_id), 'r': int(round_no), 'h': int(home_id), 'a': int(away_id),
        'kickoff': scheduled_at,
    })).scalar_one())
    await session.execute(text("UPDATE matches SET seed=COALESCE((SELECT season_seed FROM seasons WHERE id=:s),0)+id WHERE id=:m"), {'s': int(season_id), 'm': int(match_id)})
    return match_id


async def _create_round(session, cup_id, host_league_id, round_no, pairs, first_kickoff, season_id=None):
    if season_id is None:
        raise ValueError('season_id is required for cup event scheduling')
    created=[]
    for index,(home_id,away_id) in enumerate(pairs):
        candidate=first_kickoff + timedelta(days=index)
        free_date=await get_next_free_date(session,candidate.date(),(home_id,away_id),priority=1)
        kickoff=datetime.combine(free_date,candidate.time(),tzinfo=candidate.tzinfo)
        match_id=await _insert_match(session,cup_id,host_league_id,round_no,home_id,away_id,kickoff,season_id)
        cm_id=int((await session.execute(text("""INSERT INTO cup_matches(cup_id,round,home_club_id,away_club_id,status,match_id)
            VALUES(:cup,:round,:home,:away,'SCHEDULED',:match) RETURNING id"""), {
            'cup':int(cup_id),'round':int(round_no),'home':int(home_id),'away':int(away_id),'match':match_id})).scalar_one())
        await queue_event(session,'MATCH_CUP',kickoff,{'season_id':int(season_id),'cup_id':int(cup_id),'cup_match_id':cm_id,'match_id':match_id})
        created.append(cm_id)
    return created


async def start_cup_for_league(session, league_id, season_id=None):
    """Create one 64-team Cup with cross-league selection and tier-aware draw pots."""
    host = (await session.execute(text("SELECT id FROM leagues WHERE id=:l FOR UPDATE"),
                                  {'l': int(league_id)})).scalar_one_or_none()
    if not host:
        raise ValueError('Лига не найдена.')
    season_row = await _resolve_cup_season(session, season_id)
    actual_season_id = int(season_row['id'])
    season_number = int(season_row['season_number'])
    existing = (await session.execute(text("""SELECT id FROM cups
        WHERE season_id=:sid ORDER BY id DESC LIMIT 1"""), {'sid': actual_season_id})).scalar_one_or_none()
    if existing:
        return int(existing)

    active_leagues = (await session.execute(text("SELECT id FROM leagues WHERE status='ACTIVE' ORDER BY id"))).scalars().all()
    if not active_leagues:
        raise ValueError('Нет активных лиг для Кубка.')
    participants = await _cup_candidates(session)
    while len(participants) < 64:
        bot_id, _ = await create_test_bot_club(session)
        participants.append({'club_id': int(bot_id), 'league_id': None, 'league_name': None,
                             'tier': 'ROOKIE', 'position': 99, 'points': 0, 'reputation': 1,
                             'goals_for': 0, 'goals_against': 0, 'seed_rating': 0})
    participants = participants[:64]

    cup_id = int((await session.execute(text("""INSERT INTO cups(league_id,season,season_id,status)
        VALUES(:league,:season,:season_id,'ACTIVE') RETURNING id"""), {
        'league': int(active_leagues[0]), 'season': season_number, 'season_id': actual_season_id,
    })).scalar_one())
    seed = int(season_row.get('season_seed') or 0)
    draw_rng = make_rng(seed, 'cup_draw', cup_id, season_number)
    ranked = sorted(participants, key=lambda r: (-float(r.get('seed_rating') or 0),
                                                  -TIER_STRENGTH.get(str(r.get('tier') or 'ROOKIE').upper(), 1),
                                                  int(r.get('position') or 99), int(r['club_id'])))
    pots = [ranked[i:i + 16] for i in range(0, 64, 16)]
    for pot in pots:
        draw_rng.shuffle(pot)
    for pot_no, pot in enumerate(pots, start=1):
        for item in pot:
            await session.execute(text("""INSERT INTO cup_participants
                (cup_id,club_id,source_league_id,source_tier,qualification_position,seed_rating,draw_pot)
                VALUES(:cup,:club,:league,:tier,:position,:rating,:pot)
                ON CONFLICT(cup_id,club_id) DO NOTHING"""), {
                'cup': cup_id, 'club': int(item['club_id']), 'league': item.get('league_id'),
                'tier': str(item.get('tier') or 'ROOKIE').upper(), 'position': int(item.get('position') or 99),
                'rating': float(item.get('seed_rating') or 0), 'pot': pot_no,
            })

    # Standard 64-slot seeded bracket. Seeds 1-16 are distributed across
    # separate sections, while pot 1 faces pot 4 and pot 2 faces pot 3 in R1.
    bracket_seed_order = [
        1,64,32,33,16,49,17,48,8,57,25,40,9,56,24,41,
        4,61,29,36,13,52,20,45,5,60,28,37,12,53,21,44,
        2,63,31,34,15,50,18,47,7,58,26,39,10,55,23,42,
        3,62,30,35,14,51,19,46,6,59,27,38,11,54,22,43,
    ]
    seeded = pots[0] + pots[1] + pots[2] + pots[3]
    bracket_slots = [seeded[seed_no - 1] for seed_no in bracket_seed_order]
    pairs = [(int(bracket_slots[i]['club_id']), int(bracket_slots[i + 1]['club_id']))
             for i in range(0, 64, 2)]
    msk = timezone(timedelta(hours=3))
    first = datetime.combine(datetime.now(msk).date() + timedelta(days=1), time(17, 0), tzinfo=msk)
    await _create_round(session, cup_id, int(active_leagues[0]), 1, pairs, first, actual_season_id)
    return cup_id


async def _goalkeeper_ovr(session, club_id):
    rows = (await session.execute(text("""
        SELECT p.id,p.position,p.pace,p.shooting,p.passing,p.dribbling,p.defending,
               p.physical,p.stamina,p.mental,cp.fitness,cp.form,cp.morale
        FROM club_players cp JOIN players p ON p.id=cp.player_id
        WHERE cp.club_id=:c AND p.position='GK' AND cp.is_injured=false
        ORDER BY p.potential DESC,p.id LIMIT 3
    """), {'c': int(club_id)})).mappings().all()
    if not rows:
        return 50.0
    best = rows[0]
    p = SimpleNamespace(**dict(best))
    return float(player_rating(p))


async def shoot_penalties(session, match):
    """Simulate a five-kick shootout, then sudden death if still tied.

    The current player schema has no dedicated penalty_skill or goalkeeper_skill
    fields, so this uses the specified 75% fallback probability. A match-seeded
    RNG makes a retry produce the same shootout score.
    """
    match_id=int(match.get('id') or match.get('match_id') or 0)
    home_id=int(match['home_club_id']); away_id=int(match['away_club_id'])
    rng=make_rng(int(match.get('seed') or match_id or 1), 'cup_penalty_shootout', match_id, home_id, away_id)
    scores={home_id:0,away_id:0}

    def attempt():
        return rng.random() < 0.75

    for round_no in range(1,6):
        if attempt(): scores[home_id]+=1
        if attempt(): scores[away_id]+=1
        remaining=5-round_no
        if scores[home_id]>scores[away_id]+remaining or scores[away_id]>scores[home_id]+remaining:
            return scores[home_id],scores[away_id]
    round_no=6
    while scores[home_id]==scores[away_id] and round_no<=25:
        home_goal=attempt(); away_goal=attempt()
        if home_goal: scores[home_id]+=1
        if away_goal: scores[away_id]+=1
        if home_goal != away_goal:
            break
        round_no+=1
    if scores[home_id]==scores[away_id]:
        # Defensive termination for pathological RNG/test doubles.
        if rng.random()<0.5: scores[home_id]+=1
        else: scores[away_id]+=1
    return scores[home_id],scores[away_id]


async def penalty_winner(session, home_club_id, away_club_id, seed):
    """Compatibility wrapper for older callers; actual result is a shootout."""
    home_pen,away_pen=await shoot_penalties(session, {
        'id':int(seed), 'match_id':int(seed), 'home_club_id':int(home_club_id),
        'away_club_id':int(away_club_id), 'seed':int(seed),
    })
    return int(home_club_id if home_pen>away_pen else away_club_id)


async def record_cup_result(session, cup_match_id, home_score, away_score):
    match = (await session.execute(text("""
        SELECT id,cup_id,round,home_club_id,away_club_id,status,match_id
        FROM cup_matches WHERE id=:m FOR UPDATE
    """), {'m': int(cup_match_id)})).mappings().first()
    if not match or match['status'] == 'FINISHED':
        raise ValueError('Матч Кубка не найден или уже завершён.')
    home_score, away_score = int(home_score), int(away_score)
    if home_score < 0 or away_score < 0:
        raise ValueError('Счёт не может быть отрицательным.')
    home_pen=away_pen=None
    if home_score == away_score:
        linked_match=(await session.execute(text('SELECT id,seed,home_club_id,away_club_id FROM matches WHERE id=:m'), {'m':match.get('match_id')})).mappings().first() if match.get('match_id') else None
        if linked_match:
            home_pen,away_pen=await shoot_penalties(session,dict(linked_match))
        else:
            home_pen,away_pen=await shoot_penalties(session,{'id':0,'match_id':0,'home_club_id':match['home_club_id'],'away_club_id':match['away_club_id'],'seed':int(cup_match_id)})
        winner = int(match['home_club_id'] if home_pen>away_pen else match['away_club_id'])
    else:
        winner = int(match['home_club_id'] if home_score > away_score else match['away_club_id'])
    await session.execute(text("""
        UPDATE cup_matches
        SET home_score=:h,away_score=:a,home_penalties=:hp,away_penalties=:ap,winner_id=:w,status='FINISHED'
        WHERE id=:m
    """), {'h': home_score, 'a': away_score, 'hp':home_pen, 'ap':away_pen, 'w': winner, 'm': int(cup_match_id)})
    await session.execute(text("""
        UPDATE matches SET home_score=:h,away_score=:a,home_penalties=:hp,away_penalties=:ap,status='FINISHED',finished_at=now()
        WHERE id=(SELECT match_id FROM cup_matches WHERE id=:m)
    """), {'h': home_score, 'a': away_score, 'hp':home_pen, 'ap':away_pen, 'm': int(cup_match_id)})
    await advance_cup(session, int(match['cup_id']))
    return winner


async def advance_cup(session, cup_id):
    cup = (await session.execute(text("""
        SELECT id,league_id,status,season_id FROM cups WHERE id=:c FOR UPDATE
    """), {'c': int(cup_id)})).mappings().first()
    if not cup or cup['status'] != 'ACTIVE':
        return None
    current = int((await session.execute(text("SELECT COALESCE(MAX(round),0) FROM cup_matches WHERE cup_id=:c"), {'c': int(cup_id)})).scalar_one())
    if current <= 0:
        return None
    pending = int((await session.execute(text("""
        SELECT count(*) FROM cup_matches WHERE cup_id=:c AND round=:r AND status!='FINISHED'
    """), {'c': int(cup_id), 'r': current})).scalar_one())
    if pending:
        return None
    winners = [int(x) for x in (await session.execute(text("""
        SELECT winner_id FROM cup_matches WHERE cup_id=:c AND round=:r ORDER BY id
    """), {'c': int(cup_id), 'r': current})).scalars().all() if x]
    if len(winners) <= 1:
        await session.execute(text("UPDATE cups SET status='FINISHED' WHERE id=:c"), {'c': int(cup_id)})
        return {'finished': True, 'winner_id': winners[0] if winners else None}
    next_round = current + 1
    pairs = list(zip(winners[::2], winners[1::2]))
    base = datetime.now(timezone.utc) + timedelta(days=1)
    await _create_round(session, int(cup_id), int(cup['league_id']), next_round, pairs, base, int(cup.get('season_id') or 0) or None)
    return {'finished': False, 'round': next_round, 'matches': len(pairs)}


async def cup_screen_data(session, club_id):
    cup = (await session.execute(text("""
        SELECT c.id,c.league_id,c.season,c.status,'FM27 Cup' AS league_name
        FROM cups c
        WHERE c.status IN ('WAITING','ACTIVE')
        ORDER BY c.id DESC LIMIT 1
    """))).mappings().first()
    if not cup:
        return None
    rows = (await session.execute(text("""
        SELECT cm.round,cm.home_club_id,cm.away_club_id,cm.home_score,cm.away_score,
               cm.home_penalties,cm.away_penalties,cm.winner_id,cm.status,h.name home_name,a.name away_name
        FROM cup_matches cm JOIN clubs h ON h.id=cm.home_club_id JOIN clubs a ON a.id=cm.away_club_id
        WHERE cm.cup_id=:c ORDER BY cm.round,cm.id
    """), {'c': int(cup['id'])})).mappings().all()
    return {'cup': dict(cup), 'matches': [dict(r) for r in rows]}


def render_cup(data, club_id):
    if not data:
        return '🏆 <b>FM27 Cup</b>\n\nДля вашей лиги Кубок ещё не создан.'
    cup = data['cup']
    rows = data['matches']
    current = max((int(r['round']) for r in rows if r['status'] != 'FINISHED'), default=max((int(r['round']) for r in rows), default=1))
    lines = [f'🏆 <b>FM27 Cup · {cup["league_name"]}</b>', f'Статус: <b>{cup["status"]}</b>', f'Раунд: <b>{ROUND_LABELS.get(current, f"Раунд {current}")}</b>', '']
    next_match = None
    for r in rows:
        if r['status'] == 'SCHEDULED' and int(club_id) in (int(r['home_club_id']), int(r['away_club_id'])):
            next_match = r
            break
    if next_match:
        opponent = next_match['away_name'] if int(next_match['home_club_id']) == int(club_id) else next_match['home_name']
        lines.append(f'Следующий соперник: <b>{opponent}</b>')
    for round_no in sorted({int(r['round']) for r in rows}):
        lines.append(f'\n<b>{ROUND_LABELS.get(round_no, f"Раунд {round_no}")}</b>')
        for r in [x for x in rows if int(x['round']) == round_no]:
            score = '—' if r['home_score'] is None else f'{r["home_score"]}:{r["away_score"]}'
            if r.get('home_penalties') is not None and r.get('away_penalties') is not None:
                score += f' ({r["home_penalties"]}:{r["away_penalties"]} пен.)'
            lines.append(f'• {r["home_name"]} — {r["away_name"]} · {score}')
    return '\n'.join(lines)
