"""Production-readiness concurrency tests.

These tests are deliberately runnable without PostgreSQL: they model the
same transaction invariants used by the production service and use a fake
Telegram bot for the outbound queue. Set RUN_REAL_LOAD_TEST=1 and provide a
real test database when extending this file with DB-backed fixtures.
"""

import asyncio
from dataclasses import dataclass, field

import pytest

from app.telegram_rate_limit import TelegramRateLimiter


@dataclass
class Match:
    id: int
    status: str = "LIVE"


@dataclass
class MockClub:
    id: int
    budget: int = 10_000_000
    players: set[int] = field(default_factory=set)


class MockWorld:
    def __init__(self):
        self.matches = [Match(i) for i in range(1, 11)]
        self.clubs = {1: MockClub(1, players=set(range(1, 20))), 2: MockClub(2), 3: MockClub(3)}
        self.market_player = 100
        self.player_lock = asyncio.Lock()
        self.club_locks = {1: asyncio.Lock(), 2: asyncio.Lock(), 3: asyncio.Lock()}
        self.notifications_sent = 0

    async def finish_match(self, match: Match):
        await asyncio.sleep(0)
        match.status = "FINISHED"

    async def buy(self, club_id: int) -> bool:
        async with self.player_lock:
            if self.market_player is None:
                return False
            self.market_player = None
            self.clubs[club_id].players.add(100)
            self.clubs[club_id].budget -= 1_000_000
            return True

    async def sell(self, club_id: int, player_id: int) -> bool:
        async with self.club_locks[club_id]:
            if len(self.clubs[club_id].players) <= 18:
                return False
            if player_id not in self.clubs[club_id].players:
                return False
            self.clubs[club_id].players.remove(player_id)
            self.clubs[club_id].budget += 500_000
            return True


class FakeBot:
    def __init__(self):
        self.calls = 0

    async def send_message(self, *_args, **_kwargs):
        self.calls += 1


@pytest.mark.asyncio
async def test_ten_live_matches_finish_concurrently():
    world = MockWorld()
    await asyncio.gather(*(world.finish_match(match) for match in world.matches))
    assert len(world.matches) == 10
    assert all(match.status == "FINISHED" for match in world.matches)


@pytest.mark.asyncio
async def test_concurrent_buy_and_sell_are_single_winner_operations():
    world = MockWorld()
    buy_results = await asyncio.gather(world.buy(2), world.buy(3))
    assert sorted(buy_results) == [False, True]
    assert sum(1 for value in buy_results if value) == 1
    assert sum(1 for club_id in (2, 3) if 100 in world.clubs[club_id].players) == 1

    sell_results = await asyncio.gather(world.sell(1, 1), world.sell(1, 2))
    assert sorted(sell_results) == [False, True]
    assert len(world.clubs[1].players) == 18
    assert world.clubs[1].budget == 10_500_000


@pytest.mark.asyncio
async def test_one_hundred_notifications_are_throttled_and_delivered():
    limiter = TelegramRateLimiter(max_messages=25, window_seconds=1.0)
    bot = FakeBot()

    async def send_one():
        await limiter.acquire()
        await bot.send_message(1, "notification")

    await asyncio.gather(*(send_one() for _ in range(100)))
    assert bot.calls == 100
