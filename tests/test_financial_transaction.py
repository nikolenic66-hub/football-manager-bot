import pytest

from app.services import record_financial_transaction


class _Result:
    def __init__(self, row=None, scalar=None):
        self._row=row
        self._scalar=scalar
    def mappings(self): return self
    def first(self): return self._row
    def scalar_one_or_none(self): return self._scalar
    def scalar_one(self): return self._scalar


class _FakeSession:
    def __init__(self, budget=100):
        self.budget=budget
        self.debt=0
        self.ledger=[]
    async def execute(self, stmt, params=None):
        q=str(stmt)
        if 'FROM club_financial_transactions' in q:
            return _Result(scalar=None)
        if 'SELECT budget,COALESCE(debt' in q:
            return _Result(row={'budget':self.budget,'debt':self.debt})
        if 'UPDATE clubs SET budget=:b,debt=:d' in q:
            self.budget=params['b']; self.debt=params['d']
            return _Result()
        if 'INSERT INTO club_financial_transactions' in q:
            self.ledger.append(params)
            return _Result()
        if 'INSERT INTO club_season_finances' in q or 'UPDATE club_season_finances' in q:
            return _Result()
        raise AssertionError(f'Unexpected SQL in fake session: {q}')


@pytest.mark.asyncio
async def test_match_expense_can_create_debt_without_negative_budget():
    s=_FakeSession(100)
    balance=await record_financial_transaction(
        s, 1, 'WAGES', -250, 'wages', league_id=1, match_id=10, allow_debt=True
    )
    assert balance == 0
    assert s.budget == 0
    assert s.debt == 150
    assert s.ledger[0]['a'] == -250


@pytest.mark.asyncio
async def test_normal_expense_still_rejects_negative_balance():
    s=_FakeSession(100)
    with pytest.raises(ValueError):
        await record_financial_transaction(s, 1, 'TRANSFER_OUT', -250, 'transfer')
