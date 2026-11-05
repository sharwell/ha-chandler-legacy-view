"""The watchdog recovers work that no timeout can interrupt.

Timeouts rely on cancellation, but a wedged Bluetooth stack or proxy can swallow
it. A "zombie" call like that can only be abandoned, never joined. These tests
prove that polling still resumes, using a fake clock so nothing waits in real
time, and that the watchdog never abandons healthy work or nudges a valve it
shouldn't.
"""

from __future__ import annotations

import asyncio
from unittest.mock import Mock

from harness import (
    ConnectionTestCase,
    FakeValveClient,
    Mode,
    wait_until,
)
from test_persistent_connection import connection_module

POLL_HARD_LIMIT = connection_module.POLL_HARD_LIMIT_SECONDS
HEAL_INTERVAL = connection_module.HEAL_POLL_MIN_INTERVAL_SECONDS
ON_DEMAND_STALE = connection_module.STALE_AFTER_ON_DEMAND_SECONDS


def _zombie(operation: str) -> FakeValveClient:
    client = FakeValveClient()
    client.modes[operation] = Mode.ZOMBIE
    return client


class StuckPollTests(ConnectionTestCase):
    """A poll wedged in an uncancellable call must not block polling forever."""

    async def finish(self, poll: asyncio.Task) -> None:
        """Join an abandoned poll. It finishes its cleanup, then reports the
        cancellation the watchdog requested, which is correct behavior."""

        async with asyncio.timeout(1):
            await asyncio.gather(poll, return_exceptions=True)
        self.assertTrue(poll.done())

    async def start_stuck_poll(self, operation: str = "write") -> asyncio.Task:
        self.next_client_factory = lambda: _zombie(operation)
        poll = self.start_poll()
        # The zombie ignores both its timeout and cancellation, so the poll is
        # provably stuck once a cancellation has been swallowed.
        await wait_until(lambda: self.clients and self.client.swallowed_cancellations)
        self.assertFalse(poll.done())
        return poll

    async def test_stuck_poll_blocks_later_polls_until_abandoned(self) -> None:
        await self.start_stuck_poll()

        await self.poll_to_completion()  # skipped: another poll is running

        self.assertEqual(len(self.clients), 1)
        self.assertTrue(self.log.contains("another poll is already running", 0))

    async def test_watchdog_abandons_the_stuck_poll(self) -> None:
        stuck = await self.start_stuck_poll()
        original_lock = self.connection._lock

        self.clock.advance(POLL_HARD_LIMIT + 1)
        self.connection.watchdog_check()

        self.assertIsNot(self.connection._lock, original_lock)
        self.assertFalse(self.connection._lock.locked())
        self.assertIsNone(self.connection._poll_task)
        self.assertTrue(self.log.contains("abandoning it"))
        self.assertFalse(stuck.done())  # truly unjoinable until released

    async def test_polling_resumes_after_the_watchdog_abandons_a_poll(self) -> None:
        await self.start_stuck_poll()
        self.clock.advance(POLL_HARD_LIMIT + 1)
        self.connection.watchdog_check()
        self.next_client_factory = FakeValveClient

        await self.poll_to_completion()

        self.assertEqual(len(self.clients), 2)
        self.assertEqual(self.connection.dashboard_data.water_usage, 567)
        self.assert_idle()

    async def test_next_poll_recovers_even_before_the_watchdog_runs(self) -> None:
        await self.start_stuck_poll()
        self.clock.advance(POLL_HARD_LIMIT + 1)
        self.next_client_factory = FakeValveClient

        await self.poll_to_completion()

        self.assertEqual(len(self.clients), 2)
        self.assertEqual(self.connection.dashboard_data.water_usage, 567)

    async def test_poll_within_the_limit_is_left_alone(self) -> None:
        await self.start_stuck_poll()
        original_lock = self.connection._lock

        self.clock.advance(POLL_HARD_LIMIT - 1)
        self.connection.watchdog_check()

        self.assertIs(self.connection._lock, original_lock)
        self.assertTrue(original_lock.locked())
        self.assertFalse(self.log.contains("abandoning it"))

    async def test_released_zombie_cannot_corrupt_its_replacement(self) -> None:
        stuck = await self.start_stuck_poll()
        self.clock.advance(POLL_HARD_LIMIT + 1)
        self.connection.watchdog_check()
        self.next_client_factory = FakeValveClient
        await self.poll_to_completion()
        replacement_lock = self.connection._lock

        self.clients[0].release()  # the wedged call finally returns
        await self.finish(stuck)

        self.assertIs(self.connection._lock, replacement_lock)
        self.assertFalse(replacement_lock.locked())
        self.assert_idle()
        self.assertEqual(self.connection.dashboard_data.water_usage, 567)

    async def test_released_zombie_does_not_blind_the_watchdog_to_its_replacement(
        self,
    ) -> None:
        # The replacement poll is itself still running when the first wedged
        # call finally returns. The old poll's cleanup must not erase the
        # replacement's bookkeeping, or the watchdog could never abandon it.
        first = await self.start_stuck_poll()
        self.clock.advance(POLL_HARD_LIMIT + 1)
        self.connection.watchdog_check()
        self.next_client_factory = lambda: _zombie("start_notify")
        replacement = self.start_poll()
        await wait_until(
            lambda: len(self.clients) == 2 and self.clients[1].swallowed_cancellations
        )
        task, started = self.connection._poll_task, self.connection._poll_started
        self.assertIsNotNone(task)
        self.assertIsNotNone(started)

        self.clients[0].release()
        await self.finish(first)

        self.assertIs(self.connection._poll_task, task)
        self.assertEqual(self.connection._poll_started, started)
        self.clock.advance(POLL_HARD_LIMIT + 1)
        self.connection.watchdog_check()
        self.assertIsNone(self.connection._poll_task)
        self.clients[1].release()
        await self.finish(replacement)

    async def test_each_blocking_operation_is_recoverable(self) -> None:
        for operation in ("write", "start_notify", "stop_notify", "disconnect"):
            with self.subTest(operation=operation):
                self.clients.clear()
                self.connection._next_connection_time = None
                stuck = await self.start_stuck_poll(operation)
                self.clock.advance(POLL_HARD_LIMIT + 1)
                self.connection.watchdog_check()
                self.next_client_factory = FakeValveClient

                await self.poll_to_completion()

                self.assertFalse(self.connection._lock.locked())
                self.clients[0].release()
                await self.finish(stuck)

    async def test_repeated_wedges_recover_every_time(self) -> None:
        for _ in range(5):
            self.connection._next_connection_time = None
            stuck = await self.start_stuck_poll()
            self.clock.advance(POLL_HARD_LIMIT + 1)
            self.connection.watchdog_check()
            self.clients[-1].release()
            await self.finish(stuck)
            self.clients.clear()
            self.next_client_factory = FakeValveClient
            await self.poll_to_completion()
            self.assertEqual(self.connection.dashboard_data.water_usage, 567)
            self.clients.clear()


class StuckPersistentSessionTests(ConnectionTestCase):
    """A persistent session wedged in a call is abandoned and re-established."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.connection._persistent_poll_interval = 0.01
        self.connection._persistent_connection_enabled = True

    async def start_session(self) -> None:
        await self.poll_to_completion()
        await wait_until(lambda: self.connection._persistent_task is not None)
        await wait_until(lambda: self.client.requests.count(117) >= 3)

    async def test_healthy_session_keeps_polling(self) -> None:
        await self.start_session()
        before = self.client.requests.count(117)

        await wait_until(lambda: self.client.requests.count(117) > before + 2)

        self.assertFalse(self.connection._persistent_task.done())
        self.assertEqual(self.connection.dashboard_data.water_usage, 567)

    async def test_session_stuck_in_a_write_is_abandoned_by_the_watchdog(self) -> None:
        await self.start_session()
        session = self.connection._persistent_task
        self.client.modes["write"] = Mode.ZOMBIE
        await wait_until(lambda: self.client.swallowed_cancellations)

        self.clock.advance(POLL_HARD_LIMIT + 60)
        self.connection.watchdog_check()

        self.assertIsNone(self.connection._persistent_task)
        self.assertTrue(self.log.contains("made no progress"))
        self.assertFalse(session.done())  # unjoinable until the call returns

    async def test_polling_resumes_with_a_fresh_client_after_abandonment(self) -> None:
        await self.start_session()
        self.client.modes["write"] = Mode.ZOMBIE
        await wait_until(lambda: self.client.swallowed_cancellations)
        self.next_client_factory = FakeValveClient

        self.clock.advance(POLL_HARD_LIMIT + 60)
        self.connection.watchdog_check()
        self.connection._next_connection_time = None
        self.connection.schedule_poll()
        await wait_until(lambda: len(self.clients) == 2)
        await wait_until(lambda: self.clients[1].requests.count(117) >= 2)

        self.assertIsNotNone(self.connection._persistent_task)

    async def test_busy_but_progressing_session_is_not_abandoned(self) -> None:
        await self.start_session()
        session = self.connection._persistent_task

        # Progress is stamped on every request, so a long wall-clock gap between
        # two watchdog runs does not look like a hang while the session works.
        await wait_until(lambda: self.client.requests.count(117) >= 6)
        self.clock.advance(POLL_HARD_LIMIT - 10)
        self.connection.watchdog_check()

        self.assertIs(self.connection._persistent_task, session)

    async def test_session_progress_is_cleared_when_the_session_ends(self) -> None:
        await self.start_session()

        await self.connection.async_set_persistent_connection_enabled(False)

        self.assertIsNone(self.connection._persistent_progress)
        self.assertIsNone(self.connection._persistent_task)

    async def test_request_stuck_above_the_gatt_layer_is_still_cut_off(self) -> None:
        # Every GATT call is bounded, but the whole request has its own limit
        # too, as defense in depth against a future unbounded step.
        await self.start_session()
        first = self.client
        self.next_client_factory = FakeValveClient

        async def never(client):
            await asyncio.Event().wait()

        self.connection._async_request_dashboard = never

        await wait_until(lambda: len(self.clients) == 2, timeout=2)

        self.assertTrue(self.log.contains("Persistent poll of valve"))
        self.assertTrue(first.disconnected)

    async def test_hung_dashboard_request_reconnects_without_the_watchdog(self) -> None:
        await self.start_session()
        self.client.modes["write"] = Mode.HANG  # bounded, so no watchdog needed
        self.next_client_factory = FakeValveClient

        await wait_until(lambda: len(self.clients) == 2, timeout=2)
        await wait_until(lambda: self.clients[1].requests.count(117) >= 2)

        self.assertTrue(self.clients[0].disconnected)
        self.assertEqual(self.connection.dashboard_data.water_usage, 567)


class SelfHealTests(ConnectionTestCase):
    """The watchdog nudges an idle valve, but only when that is warranted."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.connection.schedule_poll = Mock()

    def go_stale(self) -> None:
        self.clock.advance(ON_DEMAND_STALE + 1)

    async def test_stale_idle_valve_gets_a_poll(self) -> None:
        self.go_stale()

        self.connection.watchdog_check()

        self.connection.schedule_poll.assert_called_once_with()

    async def test_fresh_on_demand_valve_is_left_alone(self) -> None:
        self.connection.watchdog_check()

        self.connection.schedule_poll.assert_not_called()

    async def test_heal_polls_are_rate_limited(self) -> None:
        self.go_stale()
        self.connection.watchdog_check()

        self.clock.advance(HEAL_INTERVAL - 1)
        self.connection.watchdog_check()
        self.assertEqual(self.connection.schedule_poll.call_count, 1)

        self.clock.advance(2)
        self.connection.watchdog_check()
        self.assertEqual(self.connection.schedule_poll.call_count, 2)

    async def test_persistent_mode_idle_valve_gets_a_poll_even_when_fresh(self) -> None:
        self.connection._persistent_connection_enabled = True

        self.connection.watchdog_check()

        self.connection.schedule_poll.assert_called_once_with()

    async def test_valve_with_a_running_poll_is_not_nudged(self) -> None:
        self.go_stale()
        await self.connection._lock.acquire()

        self.connection.watchdog_check()

        self.connection.schedule_poll.assert_not_called()
        self.connection._lock.release()

    async def test_valve_with_a_live_session_is_not_nudged(self) -> None:
        self.go_stale()
        session = asyncio.create_task(asyncio.Event().wait())
        self.connection._persistent_task = session
        self.addCleanup(session.cancel)

        self.connection.watchdog_check()

        self.connection.schedule_poll.assert_not_called()

    async def test_valve_that_is_out_of_range_is_not_nudged(self) -> None:
        self.go_stale()
        self.connection.mark_unavailable()

        self.connection.watchdog_check()

        self.connection.schedule_poll.assert_not_called()

    async def test_unloaded_connection_does_nothing(self) -> None:
        self.go_stale()
        await self.connection.async_unload()

        self.connection.watchdog_check()

        self.connection.schedule_poll.assert_not_called()

    async def test_watchdog_check_is_idempotent_for_healthy_valves(self) -> None:
        for _ in range(20):
            self.clock.advance(30)
            self.connection.watchdog_check()

        self.connection.schedule_poll.assert_not_called()
        self.assertFalse(self.connection.data_is_stale)


class WatchdogTimerTests(ConnectionTestCase):
    """The manager owns one watchdog timer and keeps it alive."""

    async def test_manager_runs_the_watchdog_for_every_valve(self) -> None:
        manager = connection_module.ValveConnectionManager(
            self.hass, Mock(options={}, data={}), Mock(devices={}, async_add_listener=Mock())
        )
        first, second = Mock(address="a"), Mock(address="b")
        manager._connections = {"a": first, "b": second}

        manager._handle_watchdog(None)

        first.watchdog_check.assert_called_once_with()
        second.watchdog_check.assert_called_once_with()

    async def test_one_failing_valve_does_not_stop_the_others(self) -> None:
        manager = connection_module.ValveConnectionManager(
            self.hass, Mock(options={}, data={}), Mock(devices={}, async_add_listener=Mock())
        )
        broken, healthy = Mock(address="a"), Mock(address="b")
        broken.watchdog_check.side_effect = RuntimeError("boom")
        manager._connections = {"a": broken, "b": healthy}

        manager._handle_watchdog(None)

        healthy.watchdog_check.assert_called_once_with()
        self.assertTrue(self.log.contains("Watchdog failed"))
