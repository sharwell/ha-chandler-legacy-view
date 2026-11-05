"""Every GATT operation failing every way must never wedge a valve's poll.

Field failure being guarded against: one Bluetooth call stalled, the poll that
made it never finished, its lock stayed held, and every later poll was skipped
for days while sensors kept showing their last value.

The invariant checked for each (operation, failure) pair is the same: the poll
returns promptly, the lock is free, the client was disconnected, and a following
poll runs normally.
"""

from __future__ import annotations

import asyncio

from harness import (
    AsyncServicesClient,
    ConnectionTestCase,
    DASHBOARD,
    DEVICE_LIST,
    FakeValveClient,
    Mode,
    RESET,
    wait_until,
)

# Operations a normal poll performs, and the failures a Bluetooth link can show.
POLL_OPERATIONS = ("write", "start_notify", "stop_notify", "disconnect")
BOUNDED_FAILURES = (Mode.RAISE, Mode.HANG)

# The warning each hung operation must produce. The poll also has an overall
# deadline that would eventually cut any hang short, so recovery alone does not
# prove the per-call bound works: the specific timeout has to have fired.
BOUND_WARNING = {
    "write": "write to valve",
    "start_notify": "notification subscribe",
    "stop_notify": "notification unsubscribe",
    "disconnect": "Timed out disconnecting from valve",
    "get_services": "GATT service discovery",
}
OVERALL_DEADLINE_WARNING = "Timed out exchanging data"


class HealthyPollTests(ConnectionTestCase):
    """Baseline: the fake valve really does deliver data through the real code."""

    async def test_poll_fetches_a_dashboard_and_disconnects(self) -> None:
        await self.poll_to_completion()

        self.assertEqual(self.client.requests[:2], [DEVICE_LIST, DASHBOARD])
        self.assertEqual(self.connection.dashboard_data.water_usage, 567)
        self.assertTrue(self.client.disconnected)
        self.assertEqual(self.connection_state, [True, False])
        self.assert_idle()

    async def test_successful_poll_records_when_data_arrived(self) -> None:
        self.assertIsNone(self.connection.last_data_update)

        await self.poll_to_completion()

        self.assertIsNotNone(self.connection.last_data_update)
        self.assertFalse(self.connection.data_is_stale)

    async def test_poll_after_poll_reuses_nothing_stale(self) -> None:
        await self.poll_to_completion()
        self.next_client_factory = lambda: _client_with_usage(900)

        await self.poll_to_completion()

        self.assertEqual(len(self.clients), 2)
        self.assertEqual(self.connection.dashboard_data.water_usage, 900)


def _client_with_usage(usage: int) -> FakeValveClient:
    client = FakeValveClient()
    client.usage = usage
    return client


class GattFailureMatrixTests(ConnectionTestCase):
    """Fail each operation each way; the poll must always recover."""

    async def assert_recovers(self, operation: str, mode: Mode) -> None:
        # The first client misbehaves; the next one is healthy.
        def factory() -> FakeValveClient:
            client = FakeValveClient()
            if not self.clients:
                client.modes[operation] = mode
            return client

        self.next_client_factory = factory

        await self.poll_to_completion()

        self.assert_idle()
        self.assertFalse(self.connection.data_is_stale)
        if mode is Mode.HANG:
            self.assertTrue(
                self.log.contains(BOUND_WARNING[operation]),
                f"the per-call bound for {operation} never fired",
            )
            self.assertFalse(
                self.log.contains(OVERALL_DEADLINE_WARNING),
                f"{operation} fell through to the overall poll deadline",
            )
        # A failed poll must not leave the lock held: the next poll just works.
        await self.poll_to_completion()
        self.assertEqual(len(self.clients), 2)
        self.assertEqual(self.connection.dashboard_data.water_usage, 567)
        self.assert_idle()

    async def test_each_operation_failing_each_way_recovers(self) -> None:
        for operation in POLL_OPERATIONS:
            for mode in BOUNDED_FAILURES:
                with self.subTest(operation=operation, mode=mode.value):
                    await self.reset_for_next_case()
                    await self.assert_recovers(operation, mode)

    async def reset_for_next_case(self) -> None:
        """Start each subtest from a clean connection and an empty client list."""

        for client in self.clients:
            client.release()
        self.clients.clear()
        self.connection._dashboard_data = None
        self.connection._last_dashboard_monotonic = None
        self.connection._next_connection_time = None
        self.log.clear()

    async def test_hung_write_does_not_deliver_data_but_releases_the_lock(self) -> None:
        self.next_client_factory = lambda: _hung("write")

        await self.poll_to_completion()

        self.assertIsNone(self.connection.dashboard_data)
        self.assertTrue(self.client.disconnected)
        self.assertTrue(self.log.contains(BOUND_WARNING["write"]))
        self.assertFalse(self.log.contains(OVERALL_DEADLINE_WARNING))
        self.assert_idle()

    async def test_hung_notify_subscribe_does_not_deliver_data(self) -> None:
        self.next_client_factory = lambda: _hung("start_notify")

        await self.poll_to_completion()

        self.assertIsNone(self.connection.dashboard_data)
        self.assertTrue(self.client.disconnected)
        self.assertTrue(self.log.contains(BOUND_WARNING["start_notify"]))
        self.assertFalse(self.log.contains(OVERALL_DEADLINE_WARNING))
        self.assert_idle()

    async def test_hung_notify_unsubscribe_still_keeps_the_data(self) -> None:
        # The exchange succeeded; only the courtesy unsubscribe stalled.
        self.next_client_factory = lambda: _hung("stop_notify")

        await self.poll_to_completion()

        self.assertEqual(self.connection.dashboard_data.water_usage, 567)
        self.assert_idle()

    async def test_hung_disconnect_is_abandoned_not_waited_on_forever(self) -> None:
        self.next_client_factory = lambda: _hung("disconnect")

        await self.poll_to_completion()

        self.assertEqual(self.connection.dashboard_data.water_usage, 567)
        self.assertEqual(self.connection_state, [True, False])
        self.assert_idle()

    async def test_failed_reset_before_disconnect_still_disconnects(self) -> None:
        # RESET is the last write before disconnect; make only that one fail.
        class ResetFails(FakeValveClient):
            async def write_gatt_char(self, uuid, payload, response=True):
                if payload[0] == RESET:
                    self.calls["write"] += 1
                    raise OSError("reset failed")
                await super().write_gatt_char(uuid, payload, response)

        self.next_client_factory = ResetFails

        await self.poll_to_completion()

        self.assertTrue(self.client.disconnected)
        self.assertEqual(self.connection.dashboard_data.water_usage, 567)
        self.assert_idle()


def _hung(operation: str) -> FakeValveClient:
    client = FakeValveClient()
    client.modes[operation] = Mode.HANG
    return client


class ServiceDiscoveryFailureTests(ConnectionTestCase):
    """The ``get_services()`` path used by older Bleak versions."""

    async def test_hung_service_discovery_releases_the_poll(self) -> None:
        def factory() -> AsyncServicesClient:
            client = AsyncServicesClient()
            client.modes["get_services"] = Mode.HANG
            return client

        self.next_client_factory = factory

        await self.poll_to_completion()

        self.assertIsNone(self.connection.dashboard_data)
        self.assertTrue(self.client.disconnected)
        self.assertTrue(self.log.contains(BOUND_WARNING["get_services"]))
        self.assertFalse(self.log.contains(OVERALL_DEADLINE_WARNING))
        self.assert_idle()

    async def test_raising_service_discovery_releases_the_poll(self) -> None:
        def factory() -> AsyncServicesClient:
            client = AsyncServicesClient()
            client.modes["get_services"] = Mode.RAISE
            return client

        self.next_client_factory = factory

        await self.poll_to_completion()

        self.assertIsNone(self.connection.dashboard_data)
        self.assert_idle()

    async def test_healthy_async_service_discovery_works(self) -> None:
        self.next_client_factory = AsyncServicesClient

        await self.poll_to_completion()

        self.assertEqual(self.connection.dashboard_data.water_usage, 567)
        self.assertGreaterEqual(self.client.calls["get_services"], 1)


class ConnectFailureTests(ConnectionTestCase):
    """Connecting is bounded by the integration, not just by the library."""

    async def test_connect_that_never_returns_times_out(self) -> None:
        async def never(*args, **kwargs):
            await asyncio.Event().wait()

        self.establish.side_effect = never

        await self.poll_to_completion()

        self.assertEqual(self.clients, [])
        self.assert_idle()

    async def test_connect_that_raises_releases_the_poll(self) -> None:
        self.establish.side_effect = OSError("no adapter")

        await self.poll_to_completion()

        self.assert_idle()

    async def test_connect_failure_sets_a_cooldown(self) -> None:
        self.establish.side_effect = OSError("no adapter")

        await self.poll_to_completion()

        self.assertIsNotNone(self.connection._next_connection_time)

    async def test_recovers_once_the_valve_is_reachable_again(self) -> None:
        self.establish.side_effect = OSError("out of range")
        await self.poll_to_completion()
        self.establish.side_effect = self._establish

        await self.poll_to_completion()

        self.assertEqual(self.connection.dashboard_data.water_usage, 567)


class PollDeadlineTests(ConnectionTestCase):
    """The whole post-connect exchange has a hard deadline of its own."""

    async def test_exchange_that_never_finishes_is_cut_off(self) -> None:
        async def never(client) -> bool:
            await asyncio.Event().wait()

        self.connection._async_fetch_device_information = never

        await self.poll_to_completion()

        self.assertTrue(self.client.disconnected)
        self.assert_idle()

    async def test_deadline_still_disconnects_and_reports_link_down(self) -> None:
        async def never(client) -> bool:
            await asyncio.Event().wait()

        self.connection._async_fetch_device_information = never

        await self.poll_to_completion()

        self.assertEqual(self.connection_state, [True, False])

    async def test_unanswered_requests_end_in_a_clean_poll(self) -> None:
        # The valve accepts writes but never replies: every wait times out.
        self.next_client_factory = _silent_client

        await self.poll_to_completion()

        self.assertIsNone(self.connection.dashboard_data)
        self.assertTrue(self.client.disconnected)
        self.assert_idle()


def _silent_client() -> FakeValveClient:
    client = FakeValveClient()
    client.answer = False
    return client


class RepeatedFailureTests(ConnectionTestCase):
    """A valve that keeps failing must never accumulate stuck state."""

    async def test_many_consecutive_hung_polls_leave_nothing_behind(self) -> None:
        self.next_client_factory = lambda: _hung("write")

        for _ in range(10):
            await self.poll_to_completion()
            self.assert_idle()

        self.assertEqual(len(self.clients), 10)
        self.assertTrue(all(client.disconnected for client in self.clients))

    async def test_polls_alternating_good_and_bad_all_complete(self) -> None:
        for index in range(8):
            self.next_client_factory = (
                (lambda: _hung("start_notify")) if index % 2 else FakeValveClient
            )
            await self.poll_to_completion()
            self.assert_idle()

        self.assertEqual(self.connection.dashboard_data.water_usage, 567)

    async def test_concurrent_poll_requests_do_not_stack(self) -> None:
        first = self.start_poll()
        await wait_until(lambda: self.connection._lock.locked())

        # Overlapping requests are dropped, not queued behind the lock.
        await self.poll_to_completion()
        await first

        self.assertEqual(len(self.clients), 1)
        self.assert_idle()
