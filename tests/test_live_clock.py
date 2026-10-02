from app.game.live_clock import LiveClock, SECONDS_PER_GAME_MINUTE, HALFTIME_SECONDS, FIRST_HALF_SECONDS, SECOND_HALF_SECONDS

def test_normal_live_timing_constants():
    assert SECONDS_PER_GAME_MINUTE == 2
    assert FIRST_HALF_SECONDS == 90
    assert HALFTIME_SECONDS == 60
    assert SECOND_HALF_SECONDS == 90
    assert FIRST_HALF_SECONDS + HALFTIME_SECONDS + SECOND_HALF_SECONDS == 240

def test_first_half_clock():
    assert LiveClock('FIRST_HALF', 0).minute == 0
    assert LiveClock('FIRST_HALF', 1.9).minute == 0
    assert LiveClock('FIRST_HALF', 2).minute == 1
    assert LiveClock('FIRST_HALF', 89.9).minute == 44
    assert LiveClock('FIRST_HALF', 90).minute == 45

def test_halftime_clock():
    assert LiveClock('HALFTIME', 0).minute == 45
    assert LiveClock('HALFTIME', 59).minute == 45

def test_second_half_clock():
    assert LiveClock('SECOND_HALF', 0).minute == 45
    assert LiveClock('SECOND_HALF', 2).minute == 46
    assert LiveClock('SECOND_HALF', 88).minute == 89
    assert LiveClock('SECOND_HALF', 90).minute == 90

def test_finished_clock():
    assert LiveClock('FINISHED', 0).minute == 90
