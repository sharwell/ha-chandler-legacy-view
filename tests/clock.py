"""Advance the real event loop's deadlines together with HA's UTC clock."""

import asyncio
from datetime import timedelta

from homeassistant.util import dt as dt_util


class Clock:
    def __init__(self, monkeypatch):
        self.loop = asyncio.get_running_loop()
        self.offset = 0.0
        real_time = self.loop.time
        started = dt_util.utcnow()
        monkeypatch.setattr(self.loop, "time", lambda: real_time() + self.offset)
        monkeypatch.setattr(dt_util, "utcnow", lambda: started + timedelta(seconds=self.offset))

    async def drain(self):
        for _ in range(12):
            await asyncio.sleep(0)

    async def advance(self, seconds):
        self.offset += seconds
        await self.drain()

    async def run(self, awaitable, budget=120):
        task = asyncio.ensure_future(awaitable)
        start = self.loop.time()
        for _ in range(500):
            await self.drain()
            if task.done():
                return task.result()
            # Timer inspection belongs to this controlled-clock driver. The
            # production deadlines, retries, and cleanup budgets stay unchanged.
            deadlines = [handle.when() for handle in self.loop._scheduled
                         if not handle.cancelled()]
            if not deadlines:
                break
            delta = max(0, min(deadlines) - self.loop.time()) + 0.001
            if self.loop.time() + delta - start > budget:
                break
            await self.advance(delta)
        task.cancel()
        await self.drain()
        raise AssertionError("Operation did not complete within the virtual-time budget")
