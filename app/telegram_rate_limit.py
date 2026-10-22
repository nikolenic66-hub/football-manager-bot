"""Process-wide Telegram outbound rate limiter.

Telegram's published global bot limit is approximately 30 messages/sec.
Keep an application-wide safety margin at 25 messages/sec. This limiter is
per Python process; multi-process deployments must use a shared limiter.
"""

import asyncio
import time
from collections import deque


class TelegramRateLimiter:
    def __init__(self, max_messages: int = 25, window_seconds: float = 1.0):
        self.max_messages = int(max_messages)
        self.window_seconds = float(window_seconds)
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                cutoff = now - self.window_seconds
                while self._timestamps and self._timestamps[0] <= cutoff:
                    self._timestamps.popleft()
                if len(self._timestamps) < self.max_messages:
                    self._timestamps.append(now)
                    return
                delay = max(0.001, self._timestamps[0] + self.window_seconds - now)
            await asyncio.sleep(delay)


telegram_rate_limiter = TelegramRateLimiter(max_messages=25, window_seconds=1.0)
