"""Bound external Bluetooth work without forgetting cancellation-resistant calls."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any


class OwnedBluetoothOperations:
    """Keep a client and all outstanding external operations in one generation.

    Only external I/O is detached on a timeout. Production parsing continues
    in its original caller, and notification callbacks are fenced immediately.
    The owner cannot be released until detached I/O and disconnect both finish.
    """

    def __init__(self, changed: Callable[[], None]) -> None:
        self.client: Any = None
        self.expired = False
        self.retired = False
        self.notifications_enabled = True
        self.pending: set[asyncio.Task] = set()
        self.cleanup_task: asyncio.Task | None = None
        self._changed = changed

    @property
    def released(self) -> bool:
        """Require backend disconnect confirmation and completion of old I/O."""

        return (
            self.retired
            and not self.pending
            and (self.client is None or not self.client.is_connected)
            and (self.cleanup_task is None or self.cleanup_task.done())
        )

    def invalidate(self) -> None:
        """Prevent an expired generation from delivering application data."""

        self.expired = True
        self.notifications_enabled = False

    def guard_notification(self, handler):
        """Retain the ownership check even if subscribe completes very late."""

        def guarded(sender, data):
            if self.notifications_enabled and not self.expired and not self.retired:
                handler(sender, data)

        return guarded

    async def run(
        self,
        awaitable: Awaitable,
        timeout: float,
        *,
        connecting: bool = False,
    ):
        """Bound the wait, retaining a strong reference until the I/O finishes."""

        task = asyncio.ensure_future(awaitable)
        self.pending.add(task)

        def finished(completed):
            self.pending.discard(completed)
            if not completed.cancelled():
                # Consume exceptions even when the timed-out caller has left.
                error = completed.exception()
                if connecting and error is None:
                    self.client = completed.result()
            self._changed()

        task.add_done_callback(finished)
        try:
            done, _ = await asyncio.wait({task}, timeout=timeout)
            if not done:
                self.invalidate()
                task.cancel()
                raise TimeoutError("Bluetooth operation exceeded its deadline")
            return task.result()
        except asyncio.CancelledError:
            self.invalidate()
            task.cancel()
            raise
