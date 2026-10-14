

def test_admin_finished_league_exposes_creator_only_restart_and_resets_season():
    from pathlib import Path
    source=Path('app/handlers/admin.py').read_text(encoding='utf-8')
    block=source[source.index("async def admin_league_router"):source.index("@admin_router.message(AdminStates.league_add_club)")]
    assert "r['status']=='FINISHED' and r['creator_user_id']==c.from_user.id" in block
    assert "callback_data" not in block or 'admin:league:{lid}:restart' in block
    assert "action=='restart'" in block
    assert "DELETE FROM matches WHERE league_id=:l" in block
    assert "UPDATE league_teams SET played=0,wins=0,draws=0,losses=0,goals_for=0,goals_against=0,points=0 WHERE league_id=:l" in block
    assert "status='WAITING',current_round=0,started_at=NULL,finished_at=NULL,total_rounds=0" in block
    assert 'Лига перезапущена. Готова к новому сезону.' in block
