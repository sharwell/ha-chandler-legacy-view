"""Bounded waits, and an unload or reload that can never hang.

The field failure was compounded by unload: it waited on the same lock the stuck
poll held, so reloading the integration could not recover either. Every wait on
Bluetooth work now has a ceiling, proven here against hung and zombie calls.
"""

from __future__ import annotations

import asyncio
from unittest.mock import Mock

from harness import ConnectionTestCase, FakeValveClient, Mode, wait_until
from test_persistent_connection import connection_module

wait_for_cleanup = connection_module._async_wait_for_cleanup
bounded = connection_module._async_bounded


class BoundedCallTests(ConnectionTestCase):
    """``_async_bounded`` turns a stall into an ordinary, handled failure."""

    async def test_returns_the_result(self) -> None:
        async def work() -> int:
            return 7

        self.assertEqual(await bounded(work(), 1, "work"), 7)

    async def test_raises_timeout_when_the_call_stalls(self) -> None:
        with self.assertRaises(TimeoutError):
            await bounded(asyncio.Event().wait(), 0.02, "a stalled call")

        self.assertTrue(self.log.contains("a stalled call"))

    async def test_other_errors_pass_through_untouched(self) -> None:
        async def fail() -> None:
            raise OSError("link lost")

        with self.assertRaises(OSError):
            await bounded(fail(), 1, "failing call")

    async def test_caller_cancellation_is_not_turned_into_a_timeout(self) -> None:
        task = asyncio.create_task(bounded(asyncio.Event().wait(), 5, "slow call"))
        await asyncio.sleep(0)

        task.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_message_shows_sub_second_limits_honestly(self) -> None:
        with self.assertRaises(TimeoutError):
            await bounded(asyncio.Event().wait(), 0.05, "a stalled call")

        self.assertTrue(self.log.contains("0.05 seconds"))


class WaitForCleanupTests(ConnectionTestCase):
    """``_async_wait_for_cleanup`` drains cleanup but can be bounded."""

    async def test_waits_for_the_task_without_a_timeout(self) -> None:
        done = []

        async def cleanup() -> None:
            await asyncio.sleep(0.02)
            done.append(True)

        await wait_for_cleanup(asyncio.create_task(cleanup()))

        self.assertEqual(done, [True])

    async def test_propagates_a_cleanup_failure(self) -> None:
        async def cleanup() -> None:
            raise ValueError("cleanup failed")

        with self.assertRaises(ValueError):
            await wait_for_cleanup(asyncio.create_task(cleanup()))

    async def test_finished_task_returns_immediately(self) -> None:
        async def cleanup() -> None:
            return None

        task = asyncio.create_task(cleanup())
        await task

        await wait_for_cleanup(task, 0.01)

    async def test_abandons_cleanup_that_outlives_the_timeout(self) -> None:
        release = asyncio.Event()

        async def cleanup() -> None:
            await release.wait()

        task = asyncio.create_task(cleanup())

        async with asyncio.timeout(1):
            await wait_for_cleanup(task, 0.05)
        await asyncio.gather(task, return_exceptions=True)

        self.assertTrue(task.cancelled())
        self.assertTrue(self.log.contains("Abandoning Bluetooth cleanup"))

    async def test_a_zombie_cleanup_is_abandoned_without_being_joined(self) -> None:
        release = asyncio.Event()

        async def cleanup() -> None:
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    pass

        task = asyncio.create_task(cleanup())
        await asyncio.sleep(0)

        async with asyncio.timeout(1):
            await wait_for_cleanup(task, 0.05)  # returns despite the zombie

        self.assertFalse(task.done())
        release.set()
        await task

    async def test_caller_cancellation_still_drains_unbounded_cleanup(self) -> None:
        release = asyncio.Event()
        finished = []

        async def cleanup() -> None:
            await release.wait()
            finished.append(True)

        cleanup_task = asyncio.create_task(cleanup())
        waiter = asyncio.create_task(wait_for_cleanup(cleanup_task))
        await asyncio.sleep(0)
        waiter.cancel()
        await asyncio.sleep(0)

        self.assertFalse(waiter.done())  # still draining
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await waiter

        self.assertEqual(finished, [True])

    async def test_repeated_cancellation_still_drains(self) -> None:
        release = asyncio.Event()

        async def cleanup() -> None:
            await release.wait()

        waiter = asyncio.create_task(wait_for_cleanup(asyncio.create_task(cleanup())))
        for _ in range(3):
            await asyncio.sleep(0)
            waiter.cancel()
        await asyncio.sleep(0)
        self.assertFalse(waiter.done())

        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await waiter

    async def test_cancellation_during_a_bounded_wait_still_propagates(self) -> None:
        release = asyncio.Event()

        async def cleanup() -> None:
            await release.wait()

        waiter = asyncio.create_task(
            wait_for_cleanup(asyncio.create_task(cleanup()), 5)
        )
        await asyncio.sleep(0)
        waiter.cancel()
        release.set()

        with self.assertRaises(asyncio.CancelledError):
            await waiter


class UnloadBoundTests(ConnectionTestCase):
    """Unload finishes within its grace period whatever the Bluetooth stack does."""

    async def assert_unload_is_bounded(self) -> None:
        async with asyncio.timeout(2):
            await self.connection.async_unload()

    async def test_unload_while_idle_is_immediate(self) -> None:
        await self.assert_unload_is_bounded()

    async def test_unload_waits_for_a_healthy_poll_to_finish(self) -> None:
        poll = self.start_poll()
        await wait_until(lambda: self.connection._lock.locked())

        await self.assert_unload_is_bounded()
        await poll

        self.assertTrue(self.client.disconnected)
        self.assertEqual(self.connection.dashboard_data.water_usage, 567)

    async def test_unload_does_not_hang_on_a_zombie_poll(self) -> None:
        # This is the reload that could not recover in the field.
        self.next_client_factory = lambda: _zombie("write")
        poll = self.start_poll()
        await wait_until(lambda: self.clients and self.client.swallowed_cancellations)

        await self.assert_unload_is_bounded()

        self.assertTrue(self.log.contains("Abandoning a stuck poll"))
        self.client.release()
        await asyncio.gather(poll, return_exceptions=True)

    async def test_unload_does_not_hang_on_a_zombie_disconnect(self) -> None:
        self.next_client_factory = lambda: _zombie("disconnect")
        poll = self.start_poll()
        await wait_until(lambda: self.clients and self.client.swallowed_cancellations)

        await self.assert_unload_is_bounded()

        self.client.release()
        await asyncio.gather(poll, return_exceptions=True)

    async def test_unload_after_the_poll_is_cancelled_still_completes(self) -> None:
        self.next_client_factory = lambda: _zombie("start_notify")
        poll = self.start_poll()
        await wait_until(lambda: self.clients and self.client.swallowed_cancellations)

        poll.cancel()
        await self.assert_unload_is_bounded()

        self.client.release()
        await asyncio.gather(poll, return_exceptions=True)

    async def test_unload_blocks_further_polls(self) -> None:
        await self.assert_unload_is_bounded()

        await self.poll_to_completion()

        self.assertEqual(self.clients, [])

    async def test_unload_twice_is_harmless(self) -> None:
        await self.assert_unload_is_bounded()
        await self.assert_unload_is_bounded()


class PersistentStopBoundTests(ConnectionTestCase):
    """Switching persistent mode off, or unloading, never waits unboundedly."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.connection._persistent_poll_interval = 0.01
        self.connection._persistent_connection_enabled = True

    async def start_session(self) -> None:
        await self.poll_to_completion()
        await wait_until(lambda: self.connection._persistent_task is not None)
        await wait_until(lambda: self.client.requests.count(117) >= 2)

    async def test_switch_off_completes_with_a_healthy_link(self) -> None:
        await self.start_session()

        async with asyncio.timeout(2):
            await self.connection.async_set_persistent_connection_enabled(False)

        self.assertTrue(self.client.disconnected)
        self.assertFalse(self.connection.persistent_connection_enabled)

    async def test_switch_off_is_bounded_when_disconnect_is_a_zombie(self) -> None:
        await self.start_session()
        self.client.modes["disconnect"] = Mode.ZOMBIE

        async with asyncio.timeout(2):
            await self.connection.async_set_persistent_connection_enabled(False)

        self.assertFalse(self.connection.persistent_connection_enabled)
        self.assertIsNone(self.connection._persistent_stop_task)

    async def test_unload_is_bounded_when_disconnect_is_a_zombie(self) -> None:
        await self.start_session()
        self.client.modes["disconnect"] = Mode.ZOMBIE

        async with asyncio.timeout(3):
            await self.connection.async_unload()

    async def test_stuck_stop_task_is_abandoned_by_the_watchdog(self) -> None:
        stuck = asyncio.create_task(asyncio.Event().wait())
        self.addCleanup(stuck.cancel)
        self.connection._persistent_stop_task = stuck
        self.connection._persistent_stop_started = self.clock.now

        self.clock.advance(connection_module.POLL_HARD_LIMIT_SECONDS + 1)
        self.connection.watchdog_check()

        self.assertIsNone(self.connection._persistent_stop_task)
        self.assertTrue(self.log.contains("cleanup"))


class ManagerUnloadTests(ConnectionTestCase):
    """The manager unloads every valve even if one is wedged."""

    async def test_manager_unload_cancels_both_timers(self) -> None:
        cancel_poll, cancel_watchdog = Mock(), Mock()
        timers = iter([cancel_poll, cancel_watchdog])
        discovery = Mock(devices={}, async_add_listener=Mock(return_value=Mock()))
        manager = connection_module.ValveConnectionManager(
            self.hass, Mock(options={}, data={}, entry_id="e"), discovery
        )
        with unittest_patch_timer(lambda *args, **kwargs: next(timers)):
            await manager.async_setup()

        await manager.async_unload()

        cancel_poll.assert_called_once_with()
        cancel_watchdog.assert_called_once_with()

    async def test_manager_unload_survives_a_zombie_valve(self) -> None:
        self.next_client_factory = lambda: _zombie("write")
        poll = self.start_poll()
        await wait_until(lambda: self.clients and self.client.swallowed_cancellations)
        manager = connection_module.ValveConnectionManager(
            self.hass, Mock(options={}, data={}), Mock()
        )
        manager._connections[self.connection.address] = self.connection

        async with asyncio.timeout(5):
            await manager.async_unload()

        self.assertEqual(manager._connections, {})
        self.client.release()
        await asyncio.gather(poll, return_exceptions=True)


def unittest_patch_timer(side_effect):
    from unittest.mock import patch

    return patch.object(
        connection_module, "async_track_time_interval", side_effect=side_effect
    )


def _zombie(operation: str) -> FakeValveClient:
    client = FakeValveClient()
    client.modes[operation] = Mode.ZOMBIE
    return client
