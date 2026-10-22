from pathlib import Path


SERVICES = Path(__file__).parents[1] / 'app' / 'services.py'


def test_quick_test_uses_admin_owned_club_and_does_not_create_a_replacement_club():
    source = SERVICES.read_text(encoding='utf-8')
    start = source.index('async def start_quick_test_league')
    end = source.index('\n\nasync def auto_join_rookie_league', start)
    block = source[start:end]

    assert 'SELECT id FROM clubs WHERE owner_user_id=:u ORDER BY is_test ASC,id LIMIT 1' in block
    assert "'creator': int(owner)" in block
    assert "{'l':league_id,'c':int(player_club)}" in block
    assert 'create_test_bot_club' not in block
    assert 'start_global_competitions=False' in block


def test_quick_test_handles_null_season_seed_without_int_none_error():
    source = SERVICES.read_text(encoding='utf-8')
    assert "scalar_one_or_none() or 0)" in source
    assert "season_seed=int((await s.execute(text(\"SELECT season_seed FROM seasons WHERE id=:s\"), {'s':int(season_id)})).scalar_one_or_none() or 0)" in source


def test_quick_test_falls_back_to_latest_season_and_handles_null_seed():
    source = SERVICES.read_text(encoding='utf-8')
    start = source.index('async def _start_league_by_id')
    end = source.index('\n\nasync def start_quick_test_league', start)
    block = source[start:end]
    assert "SELECT id FROM seasons ORDER BY id DESC LIMIT 1" in block
    assert "raise ValueError('Нельзя запустить быстрый тест: в базе ещё не создан сезон.')" in block
    assert "scalar_one_or_none() or 0)" in block
    assert "'season_id':int(season_id)" in block
