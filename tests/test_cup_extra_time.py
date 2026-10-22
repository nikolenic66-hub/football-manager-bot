from app.game.engine import simulate_second_half
from app.game.tactics import Tactics


def test_extra_time_is_opt_in_and_only_when_score_is_tied_after_regulation():
    # Empty sides make the regulation score stable while still exercising the
    # same second-half clock and return contract as a live cup match.
    common = dict(
        initial_home_score=1,
        initial_away_score=1,
        ai_commentary=False,
        return_injury_time=True,
        return_extra_time=True,
    )
    league_result = simulate_second_half([], [], Tactics(), Tactics(), 101, **common)
    cup_result = simulate_second_half([], [], Tactics(), Tactics(), 101, allow_extra_time=True, **common)

    assert len(league_result) == 5 and len(cup_result) == 5
    assert league_result[4] is False
    assert cup_result[4] is True
    assert cup_result[3] in range(1, 6)


def test_extra_time_events_use_conventional_display_minute_metadata():
    # No players means no random goals, but a tied cup match still runs the
    # extra-time phase without fabricating a score or touching league behavior.
    result = simulate_second_half(
        [], [], Tactics(), Tactics(), 202,
        initial_home_score=0, initial_away_score=0,
        ai_commentary=False, allow_extra_time=True,
        return_injury_time=True, return_extra_time=True,
    )
    assert result[0:2] == (0, 0)
    assert result[4] is True
    assert all(
        not event.metadata.get('extra_time')
        or 91 <= int(event.metadata.get('display_minute', 0)) <= 120
        for event in result[2]
    )


def test_fast_match_extra_time_is_opt_in_and_tracks_display_minutes():
    from app.game.engine import simulate_match
    from tests.test_engine import squad

    home, away = squad(1), squad(100)
    league_result = simulate_match('Home', 'Away', home, away, Tactics(), Tactics(), 4)
    cup_result = simulate_match('Home', 'Away', home, away, Tactics(), Tactics(), 4, allow_extra_time=True)

    assert league_result.extra_time_played is False
    assert all(not event.metadata.get('extra_time') for event in league_result.events)
    # This seed ties after regulation + stoppage, so the CUP path runs extra time.
    assert cup_result.extra_time_played is True
    extra_events = [event for event in cup_result.events if event.metadata.get('extra_time')]
    assert extra_events
    assert all(91 <= event.metadata['display_minute'] <= 120 for event in extra_events)
    assert cup_result.final_minute > 90

