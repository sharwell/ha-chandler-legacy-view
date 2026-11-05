"""Randomized fault injection against polls, wedged calls and live sessions.

The hand-written tests pin down specific failures; these throw random
combinations of them at the connection and check the properties that must hold
whatever happens. Every run is seeded, so a failure names the seed that
reproduces it (``subTest(seed=...)``).

Properties:
  * a one-shot poll always finishes promptly and always leaves the lock free;
  * after any mix of hung, failing and wedged calls, abandoning stuck work lets a
    healthy poll succeed;
  * a persistent session that breaks, however it breaks, reconnects and keeps
    delivering data.
"""

from __future__ import annotations

import asyncio
import random
from unittest.mock import patch

from harness import (
    ConnectionTestCase,
    FakeValveClient,
    Mode,
    wait_until,
)
from test_persistent_connection import connection_module

POLL_OPERATIONS = ("write", "start_notify", "stop_notify", "disconnect")
HARD_LIMIT = connection_module.POLL_HARD_LIMIT_SECONDS
SEEDS = range(25)

# Tighter real-time limits than the other suites: fuzzing runs many polls.
FUZZ_LIMITS = {
    "BLE_OPERATION_TIMEOUT_SECONDS": 0.02,
    "POLL_FETCH_TIMEOUT_SECONDS": 0.25,
    "_DEVICE_LIST_RESPONSE_TIMEOUT_SECONDS": 0.02,
    "_DASHBOARD_RESPONSE_TIMEOUT_SECONDS": 0.02,
    "_RESET_BUFFER_TIMEOUT_SECONDS": 0.02,
    "_DISCONNECT_TIMEOUT_SECONDS": 0.03,
}


def random_client(rng: random.Random, modes: tuple[Mode, ...]) -> FakeValveClient:
    """A client where each operation independently may fail in some way."""

    client = FakeValveClient()
    for operation in POLL_OPERATIONS:
        if rng.random() < 0.35:
            client.modes[operation] = rng.choice(modes)
    client.answer = rng.random() < 0.85
    return client


class FuzzCase(ConnectionTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        for name, value in FUZZ_LIMITS.items():
            patcher = patch.object(connection_module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)


class RandomPollFaultTests(FuzzCase):
    """Bounded failures (errors and hangs) never wedge a one-shot poll."""

    async def test_polls_always_finish_and_release_the_lock(self) -> None:
        for seed in SEEDS:
            with self.subTest(seed=seed):
                rng = random.Random(seed)
                self.connection._next_connection_time = None
                self.clients.clear()
                self.next_client_factory = lambda: random_client(
                    rng, (Mode.RAISE, Mode.HANG)
                )

                for _ in range(4):
                    await self.poll_to_completion(limit=2)
                    self.assert_idle()

                # Every connection was released, or at least released was tried.
                self.assertTrue(
                    all(c.disconnected or c.calls["disconnect"] for c in self.clients)
                )

    async def test_a_healthy_poll_always_works_after_a_bad_run(self) -> None:
        for seed in SEEDS:
            with self.subTest(seed=seed):
                rng = random.Random(seed)
                self.connection._next_connection_time = None
                self.connection._dashboard_data = None
                self.clients.clear()
                self.next_client_factory = lambda: random_client(
                    rng, (Mode.RAISE, Mode.HANG)
                )
                for _ in range(3):
                    await self.poll_to_completion(limit=2)

                self.next_client_factory = FakeValveClient
                await self.poll_to_completion()

                self.assertEqual(self.connection.dashboard_data.water_usage, 567)
                self.assert_idle()


class RandomWedgeTests(FuzzCase):
    """Wedged (uncancellable) calls are always recoverable via the watchdog."""

    async def test_watchdog_always_restores_polling(self) -> None:
        for seed in SEEDS:
            with self.subTest(seed=seed):
                rng = random.Random(seed)
                self.clients.clear()
                self.connection._dashboard_data = None
                self.connection._next_connection_time = None
                stuck_polls: list[asyncio.Task] = []

                for _ in range(rng.randint(1, 4)):
                    self.next_client_factory = lambda: random_client(
                        rng, (Mode.ZOMBIE, Mode.HANG, Mode.RAISE)
                    )
                    self.connection._next_connection_time = None
                    poll = self.start_poll()
                    # Either the poll finishes, or it is provably wedged.
                    await wait_until(
                        lambda: poll.done()
                        or (self.clients and self.client.swallowed_cancellations),
                        timeout=2,
                    )
                    if not poll.done():
                        stuck_polls.append(poll)
                    self.clock.advance(rng.choice((1, 60, HARD_LIMIT + 1)))
                    self.connection.watchdog_check()

                # Whatever is still stuck is now past the limit.
                self.clock.advance(HARD_LIMIT + 1)
                self.connection.watchdog_check()
                self.next_client_factory = FakeValveClient
                self.connection._next_connection_time = None
                await self.poll_to_completion(limit=2)

                self.assertEqual(self.connection.dashboard_data.water_usage, 567)
                self.assertFalse(self.connection._lock.locked())

                for client in self.clients:
                    client.release()
                async with asyncio.timeout(2):
                    await asyncio.gather(*stuck_polls, return_exceptions=True)
                self.assertFalse(self.connection._lock.locked())

    async def test_abandoned_polls_never_leave_the_lock_held_afterwards(self) -> None:
        for seed in SEEDS:
            with self.subTest(seed=seed):
                rng = random.Random(seed)
                self.clients.clear()
                self.connection._next_connection_time = None
                self.next_client_factory = lambda: random_client(
                    rng, (Mode.ZOMBIE,)
                )
                poll = self.start_poll()
                # With every operation a candidate zombie, the poll wedges as soon
                # as it reaches one; otherwise it simply finishes.
                await wait_until(
                    lambda: poll.done()
                    or (self.clients and self.client.swallowed_cancellations),
                    timeout=2,
                )
                self.clock.advance(HARD_LIMIT + 1)
                self.connection.watchdog_check()
                for client in self.clients:
                    client.release()
                async with asyncio.timeout(2):
                    await asyncio.gather(poll, return_exceptions=True)

                self.assertFalse(self.connection._lock.locked())
                self.assertIsNone(self.connection._poll_task)


class RandomSessionFaultTests(FuzzCase):
    """A persistent session recovers from whatever breaks its link."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.connection._persistent_poll_interval = 0.005
        self.connection._persistent_connection_enabled = True
        self.updates = 0

        def count(_data) -> None:
            self.updates += 1

        self.connection.add_dashboard_listener(count)

    async def wait_for_updates(self, extra: int) -> None:
        target = self.updates + extra
        await wait_until(lambda: self.updates >= target, timeout=3)

    async def test_session_always_resumes_delivering_data(self) -> None:
        for seed in range(20):
            with self.subTest(seed=seed):
                rng = random.Random(seed)
                self.connection._next_connection_time = None
                await self.connection.async_set_persistent_connection_enabled(False)
                await self.connection.async_set_persistent_connection_enabled(True)
                await self.wait_for_updates(3)

                for _ in range(rng.randint(1, 3)):
                    victim = self.client
                    victim.modes[rng.choice(("write", "start_notify"))] = rng.choice(
                        (Mode.RAISE, Mode.HANG)
                    )
                    self.next_client_factory = FakeValveClient
                    seen = len(self.clients)
                    await wait_until(lambda: len(self.clients) > seen, timeout=3)
                    await self.wait_for_updates(3)

                self.assertFalse(self.connection.data_is_stale)

    async def test_link_loss_while_idle_reconnects(self) -> None:
        await self.poll_to_completion()
        await self.wait_for_updates(2)
        before = len(self.clients)

        self.client.is_connected = False  # the valve walked out of range
        await wait_until(lambda: len(self.clients) > before, timeout=3)
        await self.wait_for_updates(2)

        self.assertFalse(self.connection.data_is_stale)
