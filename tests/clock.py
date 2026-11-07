"""Advance event-loop deadlines together with HA's UTC and local clocks."""

import asyncio
from datetime import UTC, timedelta

from homeassistant.util import dt as dt_util


class Clock:
    def __init__(self, monkeypatch, start=None):
        self.loop = asyncio.get_running_loop()
        self.offset = 0.0
        self._real_time = self.loop.time
        self._monotonic_start = self._real_time()
        if start is not None and start.tzinfo is None:
            raise ValueError("The controlled clock's start must include a time zone")
        self._utc_start = (start or dt_util.utcnow()).astimezone(UTC)
        monkeypatch.setattr(self.loop, "time", lambda: self._real_time() + self.offset)
        monkeypatch.setattr(dt_util, "utcnow", self.utcnow)
        monkeypatch.setattr(dt_util, "now", self.now)

    def utcnow(self):
        elapsed = self._real_time() - self._monotonic_start + self.offset
        return self._utc_start + timedelta(seconds=elapsed)

    def now(self, time_zone=None):
        return self.utcnow().astimezone(time_zone or dt_util.get_default_time_zone())

    async def drain(self):
        for _ in range(12):
            await asyncio.sleep(0)

    async def advance(self, seconds):
        await self.drain()
        self.offset += seconds
        await self.drain()

    async def until(self, predicate, budget=120):
        start = self.loop.time()
        for _ in range(500):
            await self.drain()
            if predicate():
                return
            # Finish nested cleanup continuations before jumping to a timer.
            if self.loop._ready:
                continue
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
        raise AssertionError("Operation did not complete within the virtual-time budget")

    async def run(self, awaitable, budget=120):
        task = asyncio.ensure_future(awaitable)
        try:
            await self.until(task.done, budget)
            return task.result()
        except AssertionError:
            task.cancel()
            await self.drain()
            raise
