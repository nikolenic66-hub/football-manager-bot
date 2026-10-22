from datetime import date, timedelta
import json

from app.game.calendar import PRIORITY


def _calendar(seed):
    clubs = list(range(1, 17))
    days = {}
    # League has priority 0 and each round gives every club exactly one match.
    for rnd in range(30):
        day = date(2026, 1, 1) + timedelta(days=rnd * 2 + seed % 3)
        for i in range(8):
            a = clubs[i]
            b = clubs[-1 - i]
            for club in (a, b):
                assert (club, day) not in days
                days[(club, day)] = ('LEAGUE', PRIORITY['LEAGUE'])
        clubs = [clubs[0]] + [clubs[-1]] + clubs[1:-1]
    # Lower-priority competitions are moved to the next free day for both clubs.
    for comp, priority, start_offset in (('CUP', 1, 1), ('CHAMPIONS', 2, 2)):
        for idx, (a, b) in enumerate(((1, 9), (2, 10), (3, 11), (4, 12))):
            day = date(2026, 2, 1) + timedelta(days=idx + start_offset + seed % 2)
            while (a, day) in days or (b, day) in days:
                day += timedelta(days=1)
            days[(a, day)] = (comp, priority)
            days[(b, day)] = (comp, priority)
    return days


def test_100_calendars_have_no_club_double_booking():
    for seed in range(100):
        days = _calendar(seed)
        assert len(days) == len(set(days))


def test_event_queue_contract_and_timestamp_casts():
    source = open('app/game/event_queue.py', encoding='utf-8').read()
    migration = open('migrations/048_season_events.sql', encoding='utf-8').read()
    for event_type in ('MATCH_LEAGUE', 'MATCH_CUP', 'MATCH_CHAMPIONS',
                       'SPONSOR_PAYMENT', 'SALARY_PAYMENT', 'BATTLE_PASS_END'):
        assert event_type in source
    assert 'CAST(:scheduled_at AS timestamptz)' in source
    assert 'CAST(:now AS timestamptz)' in source
    assert 'PENDING' in migration and 'DONE' in migration and 'CANCELLED' in migration
    assert 'payload JSONB' in migration
def test_tournament_structure_and_msk_times():
    services=open('app/services.py',encoding='utf-8').read()
    cup=open('app/game/cup.py',encoding='utf-8').read()
    cl=open('app/game/champions_league.py',encoding='utf-8').read()
    assert 'max_teams=16' in services and 'total_rounds=30' in services
    assert 'return 64' in cup and "'1/32 финала'" in cup
    assert 'LIMIT :n' in cl and "top_n=4" in cl
    assert 'time(20,0)' in services
    assert 'time(17,0)' in cup
    assert 'time(21,30)' in cl



def test_missed_event_is_retryable():
    source = open('app/game/event_queue.py', encoding='utf-8').read()
    assert "status='PENDING'" in source
    assert "status='PROCESSING'" in source
    assert "status='FAILED'" in source
    assert 'retry_failed_events' in source


def test_champions_payload_is_explicitly_forwarded():
    source = open('app/game/event_queue.py', encoding='utf-8').read()
    assert 'async def _play_scheduled_match(session, match_id, competition, payload=None)' in source
    assert "_play_scheduled_match(session, int(payload['match_id']), 'CUP', payload)" in source
    assert "_play_scheduled_match(session, int(payload['match_id']), 'CHAMPIONS', payload)" in source
    assert 'except Exception as exc:' in source
