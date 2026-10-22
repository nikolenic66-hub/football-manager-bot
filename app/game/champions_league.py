from datetime import datetime, time, timedelta, timezone
import string

from sqlalchemy import text

from app.services import create_test_bot_club
from app.game.event_queue import queue_event
from app.game.calendar import get_next_free_date
from app.game.rng import rng

TOURNAMENT_SIZE = 32
GROUP_SIZE = 4
GROUP_COUNT = TOURNAMENT_SIZE // GROUP_SIZE
GROUP_LETTERS = string.ascii_uppercase[:GROUP_COUNT]
STAGE_ORDER = {'R16': 1, 'QF': 2, 'SF': 3, 'FINAL': 4}
TIER_STRENGTH = {'LEGEND': 7, 'ELITE': 6, 'DIAMOND': 5, 'GOLD': 4, 'SILVER': 3, 'BRONZE': 2, 'ROOKIE': 1}


def _seed(cl_id, group_no, home_id, away_id):
    return int(cl_id) * 1_000_003 + int(group_no) * 10_007 + int(home_id) * 1009 + int(away_id)


def _tier_rank(tier):
    return TIER_STRENGTH.get(str(tier or 'ROOKIE').upper(), 1)


def _tournament_code(index):
    """A, B, ... Z, AA, AB ... without a hard 26-tournament limit."""
    n = int(index)
    result = ''
    while n > 0:
        n, rem = divmod(n - 1, 26)
        result = chr(65 + rem) + result
    return result or 'A'


async def _resolve_season(session, season_id=None):
    if season_id is not None:
        row = (await session.execute(text("SELECT id,season_number,season_seed FROM seasons WHERE id=:id"),
                                     {'id': int(season_id)})).mappings().first()
        if row:
            return dict(row)
        # Backward compatibility for callers that historically passed season_number.
        row = (await session.execute(text("SELECT id,season_number,season_seed FROM seasons WHERE season_number=:n ORDER BY id DESC LIMIT 1"),
                                     {'n': int(season_id)})).mappings().first()
        if row:
            return dict(row)
        raise ValueError(f'Сезон с ID/номером {season_id} не найден.')
    row = (await session.execute(text("""SELECT id,season_number,season_seed FROM seasons
        WHERE state IN ('ACTIVE','PRESEASON') ORDER BY CASE state WHEN 'ACTIVE' THEN 0 ELSE 1 END,id DESC LIMIT 1"""))).mappings().first()
    if not row:
        raise ValueError('Нет текущего сезона для запуска Лиги чемпионов.')
    return dict(row)


async def _league_candidates(session, top_n, current_season_id):
    current = (await session.execute(text("SELECT sequence_id,season_number FROM seasons WHERE id=:s"),
                                     {'s': int(current_season_id)})).mappings().first()
    snapshot_season_id = None
    if current:
        snapshot_season_id = (await session.execute(text("""SELECT s.id FROM seasons s
            WHERE s.sequence_id=:seq AND s.season_number<:number
              AND EXISTS (SELECT 1 FROM season_league_snapshots snap WHERE snap.season_id=s.id)
            ORDER BY s.season_number DESC LIMIT 1"""),
            {'seq': int(current['sequence_id']), 'number': int(current['season_number'])})).scalar_one_or_none()
    if snapshot_season_id is not None:
        # Use the immutable final table from the most recent completed season.
        rows = (await session.execute(text("""SELECT sls.league_id, sls.league_name, COALESCE(sls.tier,'ROOKIE') AS tier,
                (entry.value->>'club_id')::BIGINT AS club_id,
                COALESCE((entry.value->>'points')::INT,0) AS points,
                COALESCE((entry.value->>'played')::INT,0) AS played,
                COALESCE((entry.value->>'goals_for')::INT,0) AS goals_for,
                COALESCE((entry.value->>'goals_against')::INT,0) AS goals_against,
                COALESCE(c.reputation,0) AS reputation,entry.ordinality::INT AS position
            FROM season_league_snapshots sls
            CROSS JOIN LATERAL jsonb_array_elements(sls.standings) WITH ORDINALITY AS entry(value,ordinality)
            JOIN clubs c ON c.id=(entry.value->>'club_id')::BIGINT
            WHERE sls.season_id=:snapshot
            ORDER BY CASE UPPER(COALESCE(sls.tier,'ROOKIE'))
                WHEN 'LEGEND' THEN 7 WHEN 'ELITE' THEN 6 WHEN 'DIAMOND' THEN 5
                WHEN 'GOLD' THEN 4 WHEN 'SILVER' THEN 3 WHEN 'BRONZE' THEN 2 ELSE 1 END DESC,
                entry.ordinality ASC,COALESCE((entry.value->>'points')::INT,0) DESC,
                COALESCE((entry.value->>'goals_for')::INT,0)-COALESCE((entry.value->>'goals_against')::INT,0) DESC,
                COALESCE(c.reputation,0) DESC,sls.league_id,club_id"""),
            {'snapshot': int(snapshot_season_id)})).mappings().all()
    else:
        # First-season fallback: there is no completed snapshot yet, so use the
        # best current standings available rather than making the event impossible.
        rows = (await session.execute(text("""WITH ranked AS (
                SELECT lt.club_id,lt.league_id,l.name AS league_name,l.tier,
                    COALESCE(lt.points,0) AS points,COALESCE(lt.played,0) AS played,
                    COALESCE(lt.goals_for,0) AS goals_for,COALESCE(lt.goals_against,0) AS goals_against,
                    COALESCE(c.reputation,0) AS reputation,
                    ROW_NUMBER() OVER (PARTITION BY lt.league_id ORDER BY COALESCE(lt.points,0) DESC,
                        COALESCE(lt.goals_for,0)-COALESCE(lt.goals_against,0) DESC,
                        COALESCE(lt.goals_for,0) DESC,COALESCE(c.reputation,0) DESC,lt.club_id) AS position
                FROM league_teams lt JOIN leagues l ON l.id=lt.league_id JOIN clubs c ON c.id=lt.club_id
                WHERE l.status='ACTIVE')
            SELECT * FROM ranked ORDER BY CASE UPPER(COALESCE(tier,'ROOKIE'))
                WHEN 'LEGEND' THEN 7 WHEN 'ELITE' THEN 6 WHEN 'DIAMOND' THEN 5
                WHEN 'GOLD' THEN 4 WHEN 'SILVER' THEN 3 WHEN 'BRONZE' THEN 2 ELSE 1 END DESC,
                position ASC,points DESC,goals_for-goals_against DESC,reputation DESC,league_id,club_id"""))).mappings().all()
    candidates = [dict(row) for row in rows]
    for candidate in candidates:
        candidate['season_id'] = int(snapshot_season_id) if snapshot_season_id is not None else int(current_season_id)
    qualified = [r for r in candidates if int(r['position']) <= int(top_n)]
    seen = set()
    direct = []
    for row in qualified:
        cid = int(row['club_id'])
        if cid not in seen:
            seen.add(cid)
            row = dict(row)
            row['qualified_as'] = 'TOP4'
            direct.append(row)
    remaining = []
    for row in candidates:
        cid = int(row['club_id'])
        if cid not in seen:
            seen.add(cid)
            item = dict(row)
            item['qualified_as'] = 'WILDCARD'
            remaining.append(item)
    return direct, remaining


def _draw_groups(participants, season_seed, cl_id):
    """Four seeded pots; one team per pot per group, avoiding same-league clashes when possible."""
    ranked = sorted(participants, key=lambda r: (
        -_tier_rank(r.get('source_tier')),
        int(r.get('qualification_position') or 99),
        -int(r.get('points') or 0),
        -int(r.get('reputation') or 0),
        int(r['club_id']),
    ))
    pots = [ranked[i:i + 8] for i in range(0, 32, 8)]
    draw_rng = rng(int(season_seed or 0), 'champions_draw', int(cl_id))
    for pot in pots:
        draw_rng.shuffle(pot)
    groups = [[] for _ in range(8)]
    for pot_no, pot in enumerate(pots, start=1):
        # Try randomized group order first; prefer a group without a club from the same league.
        for item in pot:
            order = list(range(8))
            draw_rng.shuffle(order)
            source_league = item.get('source_league_id')
            eligible = [idx for idx in order if len(groups[idx]) < 4 and not any(
                source_league is not None and x.get('source_league_id') is not None
                and int(x['source_league_id']) == int(source_league) for x in groups[idx])]
            target = (eligible or [idx for idx in order if len(groups[idx]) < 4])[0]
            item['draw_pot'] = pot_no
            item['group_name'] = f'Группа {GROUP_LETTERS[target]}'
            groups[target].append(item)
    return groups


async def _insert_champions_match(session, cl_id, season_id, host_league_id, stage, round_no,
                                  home_id, away_id, kickoff, seed):
    free_date = await get_next_free_date(session, kickoff.date(), (int(home_id), int(away_id)), priority=2)
    kickoff = datetime.combine(free_date, kickoff.timetz().replace(tzinfo=None), tzinfo=kickoff.tzinfo)
    match_id = int((await session.execute(text("""INSERT INTO matches(
        season_id,league_id,round,home_club_id,away_club_id,seed,competition_type,started_at,scheduled_at,prestart_notified_at)
        VALUES(:season,:league,:round,:home,:away,:seed,'CHAMPIONS',NULL,CAST(:kickoff AS timestamptz),NULL)
        RETURNING id"""), {
        'season': int(season_id), 'league': int(host_league_id), 'round': int(round_no),
        'home': int(home_id), 'away': int(away_id), 'seed': int(seed), 'kickoff': kickoff,
    })).scalar_one())
    cm_id = int((await session.execute(text("""INSERT INTO champions_matches(
        cl_id,group_name,home_club_id,away_club_id,status,match_id)
        VALUES(:cl,:stage,:home,:away,'SCHEDULED',:match) RETURNING id"""), {
        'cl': int(cl_id), 'stage': stage, 'home': int(home_id), 'away': int(away_id), 'match': match_id,
    })).scalar_one())
    await queue_event(session, 'MATCH_CHAMPIONS', kickoff, {
        'season_id': int(season_id), 'cl_id': int(cl_id),
        'champions_match_id': cm_id, 'match_id': match_id,
    })
    return cm_id


async def start_champions_league(session, season_id=None, top_n=4):
    """Create one or more 32-team Champions League tournaments for the current season.

    The first four in each active league qualify directly. If the number of
    qualifiers is not a multiple of 32, the remaining places are filled by the
    best remaining real clubs (wildcards), and only then by bot clubs if needed.
    """
    season_row = await _resolve_season(session, season_id)
    actual_season_id = int(season_row['id'])
    season_number = int(season_row['season_number'])
    active = (await session.execute(text("""SELECT id,display_name FROM champions_league
        WHERE season_id=:s AND status='ACTIVE' ORDER BY tournament_index"""), {'s': actual_season_id})).mappings().all()
    if active:
        names = ', '.join(str(r['display_name']) for r in active)
        raise ValueError(f'Для текущего сезона уже есть активная Лига чемпионов: {names}.')

    host_league_id = (await session.execute(text("SELECT id FROM leagues WHERE status='ACTIVE' ORDER BY id LIMIT 1"))).scalar_one_or_none()
    if host_league_id is None:
        raise ValueError('Нет активных лиг для запуска Лиги чемпионов.')

    direct, wildcards = await _league_candidates(session, top_n, actual_season_id)
    if not direct:
        raise ValueError('В активных лигах нет клубов для квалификации в Лигу чемпионов.')
    # Fill the last 32-team competition with real wildcard clubs before creating bots.
    all_candidates = direct + wildcards
    capacity = ((len(direct) + TOURNAMENT_SIZE - 1) // TOURNAMENT_SIZE) * TOURNAMENT_SIZE
    participants = all_candidates[:capacity]
    while len(participants) < capacity:
        bot_id, _ = await create_test_bot_club(session)
        participants.append({
            'club_id': int(bot_id), 'league_id': None, 'league_name': None,
            'tier': 'ROOKIE', 'points': 0, 'played': 0, 'goals_for': 0,
            'goals_against': 0, 'reputation': 1, 'position': 99,
            'qualified_as': 'BOT_WILDCARD',
        })
    # Tournament A receives the strongest qualifiers, then B, C, ...
    created_ids = []
    prior_index = int((await session.execute(text("SELECT COALESCE(MAX(tournament_index),0) FROM champions_league WHERE season_id=:s"), {'s': actual_season_id})).scalar_one())
    msk = timezone(timedelta(hours=3))
    base = datetime.now(msk).replace(hour=21, minute=30, second=0, microsecond=0) + timedelta(days=1)
    for offset in range(0, len(participants), TOURNAMENT_SIZE):
        index = prior_index + offset // TOURNAMENT_SIZE + 1
        code = _tournament_code(index)
        display_name = f'ЛЧ-{code}'
        batch = [dict(x) for x in participants[offset:offset + TOURNAMENT_SIZE]]
        cl_id = int((await session.execute(text("""INSERT INTO champions_league
            (season,season_id,tournament_index,tournament_code,display_name,status)
            VALUES(:season,:season_id,:idx,:code,:name,'ACTIVE') RETURNING id"""), {
            'season': season_number, 'season_id': actual_season_id, 'idx': index,
            'code': code, 'name': display_name,
        })).scalar_one())
        created_ids.append(cl_id)
        for item in batch:
            item['source_league_id'] = item.get('league_id')
            item['source_tier'] = str(item.get('tier') or 'ROOKIE').upper()
            item['qualification_position'] = int(item.get('position') or 99)
        groups = _draw_groups(batch, int(season_row.get('season_seed') or 0), cl_id)
        for group_no, group in enumerate(groups):
            group_name = f'Группа {GROUP_LETTERS[group_no]}'
            await session.execute(text("INSERT INTO champions_groups(cl_id,group_name) VALUES(:cl,:name) ON CONFLICT DO NOTHING"),
                                  {'cl': cl_id, 'name': group_name})
            for item in group:
                await session.execute(text("""INSERT INTO champions_participants
                    (cl_id,club_id,source_league_id,source_tier,qualification_position,draw_pot,group_name,qualified_as,qualification_season_id)
                    VALUES(:cl,:club,:league,:tier,:position,:pot,:group,:qualified,:qualification_season)
                    ON CONFLICT(cl_id,club_id) DO NOTHING"""), {
                    'cl': cl_id, 'club': int(item['club_id']), 'league': item.get('source_league_id'),
                    'tier': item['source_tier'], 'position': item['qualification_position'],
                    'pot': int(item['draw_pot']), 'group': group_name, 'qualified': item.get('qualified_as','TOP4'),
                    'qualification_season': item.get('season_id') or None,
                })

        # Six matchdays, home and away: each group has 12 fixtures.
        pairings = (((0, 1), (2, 3)), ((0, 2), (3, 1)), ((0, 3), (1, 2)))
        for leg in range(2):
            for matchday, round_pairs in enumerate(pairings, start=1):
                global_matchday = matchday + leg * 3
                kickoff_day = base + timedelta(days=(global_matchday - 1) * 3)
                for group_no, group in enumerate(groups):
                    for pair_index, (left, right) in enumerate(round_pairs):
                        home = int(group[left]['club_id'])
                        away = int(group[right]['club_id'])
                        if leg:
                            home, away = away, home
                        kickoff = kickoff_day + timedelta(minutes=(group_no * 2 + pair_index) % 8 * 20)
                        await _insert_champions_match(
                            session, cl_id, actual_season_id, int(host_league_id),
                            f'Группа {GROUP_LETTERS[group_no]}', global_matchday,
                            home, away, kickoff, _seed(cl_id, global_matchday, home, away),
                        )
    return created_ids[0]


async def advance_champions_league(session, cl_id):
    cl = (await session.execute(text("""SELECT id,status,season_id,display_name FROM champions_league
        WHERE id=:c FOR UPDATE"""), {'c': int(cl_id)})).mappings().first()
    if not cl or cl['status'] != 'ACTIVE':
        return None
    stages = ['R16', 'QF', 'SF', 'FINAL']
    existing_stage = (await session.execute(text("""SELECT group_name FROM champions_matches
        WHERE cl_id=:c AND group_name IN ('R16','QF','SF','FINAL')
        ORDER BY CASE group_name WHEN 'R16' THEN 1 WHEN 'QF' THEN 2 WHEN 'SF' THEN 3 ELSE 4 END DESC
        LIMIT 1"""), {'c': int(cl_id)})).scalar_one_or_none()
    msk = timezone(timedelta(hours=3))
    base = datetime.now(msk).replace(hour=21, minute=30, second=0, microsecond=0) + timedelta(days=2)

    if existing_stage:
        pending = int((await session.execute(text("SELECT count(*) FROM champions_matches WHERE cl_id=:c AND group_name=:g AND status!='FINISHED'"),
                                            {'c': int(cl_id), 'g': existing_stage})).scalar_one())
        if pending:
            return None
        rows = (await session.execute(text("""SELECT cm.id,cm.home_club_id,cm.away_club_id,cm.home_score,cm.away_score,
                cm.winner_id,m.seed FROM champions_matches cm LEFT JOIN matches m ON m.id=cm.match_id
                WHERE cm.cl_id=:c AND cm.group_name=:g ORDER BY cm.id"""),
            {'c': int(cl_id), 'g': existing_stage})).mappings().all()
        winners = []
        from app.game.cup import shoot_penalties
        for row in rows:
            winner = row.get('winner_id')
            hp = ap = None
            if winner is None:
                if row['home_score'] is None or row['away_score'] is None:
                    continue
                if int(row['home_score']) > int(row['away_score']):
                    winner = int(row['home_club_id'])
                elif int(row['away_score']) > int(row['home_score']):
                    winner = int(row['away_club_id'])
                else:
                    hp, ap = await shoot_penalties(session, {
                        'id': int(row['id']), 'match_id': int(row['id']),
                        'home_club_id': int(row['home_club_id']), 'away_club_id': int(row['away_club_id']),
                        'seed': int(row.get('seed') or row['id']),
                    })
                    winner = int(row['home_club_id'] if hp > ap else row['away_club_id'])
                    await session.execute(text("UPDATE champions_matches SET home_penalties=:hp,away_penalties=:ap WHERE id=:id"),
                                          {'hp': hp, 'ap': ap, 'id': int(row['id'])})
                    await session.execute(text("UPDATE matches SET home_penalties=:hp,away_penalties=:ap WHERE id=(SELECT match_id FROM champions_matches WHERE id=:id)"),
                                          {'hp': hp, 'ap': ap, 'id': int(row['id'])})
                await session.execute(text("UPDATE champions_matches SET winner_id=:w WHERE id=:id"),
                                      {'w': int(winner), 'id': int(row['id'])})
            winners.append(int(winner))
        if existing_stage == 'FINAL':
            await session.execute(text("UPDATE champions_league SET status='FINISHED' WHERE id=:c"), {'c': int(cl_id)})
            return {'finished': True, 'winner_id': winners[0] if winners else None, 'tournament': cl['display_name']}
        next_stage = {'R16': 'QF', 'QF': 'SF', 'SF': 'FINAL'}[existing_stage]
        pairs = list(zip(winners[::2], winners[1::2]))
    else:
        pending = int((await session.execute(text("""SELECT count(*) FROM champions_matches
            WHERE cl_id=:c AND group_name LIKE 'Группа %' AND status!='FINISHED'"""), {'c': int(cl_id)})).scalar_one())
        if pending:
            return None
        groups = (await session.execute(text("SELECT group_name FROM champions_groups WHERE cl_id=:c ORDER BY group_name"),
                                        {'c': int(cl_id)})).scalars().all()
        standings_by_group = {}
        for group_name in groups:
            rows = (await session.execute(text("""SELECT club_id,SUM(points) points,SUM(gd) gd,SUM(gf) gf FROM (
                SELECT home_club_id club_id,
                    CASE WHEN home_score>away_score THEN 3 WHEN home_score=away_score THEN 1 ELSE 0 END points,
                    home_score-away_score gd,home_score gf
                FROM champions_matches WHERE cl_id=:c AND group_name=:g AND status='FINISHED'
                UNION ALL
                SELECT away_club_id club_id,
                    CASE WHEN away_score>home_score THEN 3 WHEN away_score=home_score THEN 1 ELSE 0 END points,
                    away_score-home_score gd,away_score gf
                FROM champions_matches WHERE cl_id=:c AND group_name=:g AND status='FINISHED'
            ) x GROUP BY club_id ORDER BY points DESC,gd DESC,gf DESC,club_id"""),
                {'c': int(cl_id), 'g': group_name})).mappings().all()
            standings_by_group[group_name] = [int(r['club_id']) for r in rows]
        if len(standings_by_group) != 8 or any(len(v) != 4 for v in standings_by_group.values()):
            return None
        pairs = []
        for index in range(0, 8, 2):
            group_a = f'Группа {GROUP_LETTERS[index]}'
            group_b = f'Группа {GROUP_LETTERS[index + 1]}'
            a, b = standings_by_group[group_a], standings_by_group[group_b]
            pairs.extend([(a[0], b[1]), (b[0], a[1])])
        next_stage = 'R16'
        # Store each club's final group position for the tournament history.
        for group_name, ordered_clubs in standings_by_group.items():
            for position, club_id in enumerate(ordered_clubs, start=1):
                await session.execute(text("UPDATE champions_participants SET group_position=:p WHERE cl_id=:c AND club_id=:club"),
                                      {'p': position, 'c': int(cl_id), 'club': int(club_id)})

    host = int((await session.execute(text("SELECT id FROM leagues WHERE status='ACTIVE' ORDER BY id LIMIT 1"))).scalar_one())
    for index, (home, away) in enumerate(pairs):
        kickoff = base + timedelta(days=index)
        await _insert_champions_match(session, int(cl_id), int(cl['season_id']), host, next_stage,
                                      90 + STAGE_ORDER[next_stage] * 10 + index,
                                      int(home), int(away), kickoff, _seed(cl_id, 90 + index, home, away))
    return {'stage': next_stage, 'matches': len(pairs), 'tournament': cl['display_name']}


async def get_active_champions(session, club_id=None):
    if club_id is not None:
        return (await session.execute(text("""SELECT cl.id,cl.season,cl.status,cl.display_name,cl.tournament_code
            FROM champions_league cl JOIN champions_participants cp ON cp.cl_id=cl.id
            WHERE cl.status='ACTIVE' AND cp.club_id=:club ORDER BY cl.id DESC LIMIT 1"""),
            {'club': int(club_id)})).mappings().first()
    return (await session.execute(text("""SELECT id,season,status,display_name,tournament_code
        FROM champions_league WHERE status='ACTIVE' ORDER BY id DESC LIMIT 1"""))).mappings().first()


async def champions_screen_data(session, club_id):
    cl = await get_active_champions(session, club_id)
    if not cl:
        # Let every manager view the active event even when their own club did not qualify.
        cl = await get_active_champions(session)
    if not cl:
        return None
    rows = (await session.execute(text("""SELECT cm.group_name,cm.home_club_id,cm.away_club_id,
        cm.home_score,cm.away_score,cm.home_penalties,cm.away_penalties,cm.status,
        h.name home_name,a.name away_name,m.scheduled_at
        FROM champions_matches cm JOIN clubs h ON h.id=cm.home_club_id JOIN clubs a ON a.id=cm.away_club_id
        LEFT JOIN matches m ON m.id=cm.match_id WHERE cm.cl_id=:cl ORDER BY
        CASE WHEN cm.group_name LIKE 'Группа %' THEN 0 ELSE 1 END,cm.group_name,cm.id"""),
        {'cl': int(cl['id'])})).mappings().all()
    return {'cl': dict(cl), 'matches': [dict(r) for r in rows]}


def render_champions(data, club_id):
    if not data:
        return '🌍 <b>Лига чемпионов</b>\n\nВаш клуб не участвует в активном турнире.'
    cl = data['cl']
    lines = [f'🌍 <b>{cl.get("display_name") or "Лига чемпионов"} · Сезон {cl["season"]}</b>', '']
    rows = data['matches']
    for group in sorted({r['group_name'] for r in rows}, key=lambda x: (0 if str(x).startswith('Группа ') else 1, str(x))):
        group_rows = [r for r in rows if r['group_name'] == group]
        if group in STAGE_ORDER:
            lines.append(f'<b>{group}</b>')
            for r in group_rows:
                score = '—' if r['home_score'] is None else f'{r["home_score"]}:{r["away_score"]}'
                if r.get('home_penalties') is not None and r.get('away_penalties') is not None:
                    score += f' ({r["home_penalties"]}:{r["away_penalties"]} пен.)'
                lines.append(f'• {r["home_name"]} — {r["away_name"]} · {score}')
            lines.append('')
            continue
        teams = sorted({(int(r['home_club_id']), r['home_name']) for r in group_rows} |
                       {(int(r['away_club_id']), r['away_name']) for r in group_rows})
        stats = {tid: [name, 0, 0, 0, 0] for tid, name in teams}
        for r in group_rows:
            if r['home_score'] is None or r['away_score'] is None:
                continue
            h, a = int(r['home_score']), int(r['away_score'])
            stats[int(r['home_club_id'])][2] += 1; stats[int(r['away_club_id'])][2] += 1
            if h > a: stats[int(r['home_club_id'])][1] += 3
            elif h < a: stats[int(r['away_club_id'])][1] += 3
            else:
                stats[int(r['home_club_id'])][1] += 1; stats[int(r['away_club_id'])][1] += 1
            stats[int(r['home_club_id'])][3] += h; stats[int(r['away_club_id'])][3] += a
            stats[int(r['home_club_id'])][4] += a; stats[int(r['away_club_id'])][4] += h
        lines.append(f'<b>{group}</b>')
        for _, row in sorted(stats.items(), key=lambda item: (-item[1][1], -(item[1][3]-item[1][4]), -item[1][3], item[1][0])):
            lines.append(f'• {row[0]} — {row[1]} очк. · {row[2]} игр.')
        for r in group_rows:
            if int(club_id) in (int(r['home_club_id']), int(r['away_club_id'])) and r['status'] == 'SCHEDULED':
                opponent = r['away_name'] if int(r['home_club_id']) == int(club_id) else r['home_name']
                lines.append(f'Следующий матч: <b>{opponent}</b>')
                break
        lines.append('')
    return '\n'.join(lines).strip()
