import asyncio
from app.game.ai_transfer import ai_transfer_action, _group_gap

def test_group_gap_detects_defense_hole():
    squad=[{'position':'GK'}]+[{'position':'CM'}]*6+[{'position':'ST'}]*4
    assert _group_gap(squad)=='DEF'

def test_budget_zero_skips():
    class R:
        def mappings(self): return self
        def all(self): return []
    class S:
        async def execute(self,*a,**k): return R()
    assert asyncio.run(ai_transfer_action({'id':1,'budget':0,'reputation':50},S())) is None
