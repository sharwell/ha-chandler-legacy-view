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
    Use on one event loop. ``changed`` is a synchronous callback invoked when
    an external task settles, including after its original caller has left.
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
        """Return whether retired I/O and cleanup are settled and the client is down.

        This uses the backend's connection state, not a physical adapter or
        proxy-slot observation.
        """

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
        """Wrap handler(sender, data), discarding calls after invalidation/retirement.

        The wrapper preserves the handler's synchronous calling convention
        and does not catch its exceptions or copy its arguments.
        """

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
        """Await external I/O for at most timeout seconds, retaining unfinished work.

        Return the awaitable's result or propagate its exception. Expiry of this
        wait or caller cancellation invalidates the generation and requests
        cancellation of the external task, without waiting for resistant I/O.
        If connecting is True, retain even a late successful result as this
        owner's client so cleanup can disconnect it. A return or raised timeout
        alone does not establish release.
        """

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
