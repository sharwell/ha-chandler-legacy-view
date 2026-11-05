"""Data freshness: when is a valve's data stale, and who finds out.

For leak detection the dangerous failure is silence mistaken for "no flow", so
the stale threshold, its transitions, and the notifications are specified
exactly here.
"""

from __future__ import annotations

from datetime import timezone
from unittest.mock import Mock

from harness import ConnectionTestCase, FakeValveClient
from test_persistent_connection import connection_module

ON_DEMAND = connection_module.STALE_AFTER_ON_DEMAND_SECONDS
PERSISTENT_MIN = connection_module.STALE_AFTER_PERSISTENT_MIN_SECONDS
PERSISTENT_INTERVALS = connection_module.STALE_AFTER_PERSISTENT_INTERVALS


class StaleThresholdTests(ConnectionTestCase):
    """The limit depends on whether a persistent connection is requested."""

    async def test_on_demand_limit_is_three_missed_polls(self) -> None:
        self.assertEqual(ON_DEMAND, 3 * 15 * 60)
        self.assertEqual(self.connection.stale_after_seconds, ON_DEMAND)

    async def test_persistent_limit_has_a_floor(self) -> None:
        await self.connection.async_set_persistent_connection_enabled(True)
        await self.connection.async_set_persistent_poll_interval(5)

        self.assertEqual(self.connection.stale_after_seconds, PERSISTENT_MIN)

    async def test_persistent_limit_scales_with_the_poll_interval(self) -> None:
        await self.connection.async_set_persistent_connection_enabled(True)

        for interval in (30, 60, 120, 300):
            with self.subTest(interval=interval):
                await self.connection.async_set_persistent_poll_interval(interval)
                self.assertEqual(
                    self.connection.stale_after_seconds,
                    max(PERSISTENT_MIN, PERSISTENT_INTERVALS * interval),
                )

    async def test_switching_persistent_mode_off_restores_the_on_demand_limit(self) -> None:
        await self.connection.async_set_persistent_connection_enabled(True)
        await self.connection.async_set_persistent_connection_enabled(False)

        self.assertEqual(self.connection.stale_after_seconds, ON_DEMAND)


class StaleTransitionTests(ConnectionTestCase):
    """Exactly at the limit is fresh; one second later is stale."""

    async def test_a_valve_that_never_answers_becomes_stale(self) -> None:
        self.clock.advance(ON_DEMAND)
        self.assertFalse(self.connection.data_is_stale)

        self.clock.advance(1)

        self.assertTrue(self.connection.data_is_stale)

    async def test_fresh_data_resets_the_clock(self) -> None:
        self.clock.advance(ON_DEMAND - 10)
        await self.poll_to_completion()

        self.clock.advance(ON_DEMAND)
        self.assertFalse(self.connection.data_is_stale)

        self.clock.advance(1)
        self.assertTrue(self.connection.data_is_stale)

    async def test_failed_polls_do_not_count_as_fresh(self) -> None:
        self.clock.advance(ON_DEMAND + 1)
        self.establish.side_effect = OSError("out of range")

        await self.poll_to_completion()

        self.assertTrue(self.connection.data_is_stale)

    async def test_an_answered_connection_without_a_dashboard_is_not_fresh(self) -> None:
        self.clock.advance(ON_DEMAND + 1)
        self.next_client_factory = _silent

        await self.poll_to_completion()

        self.assertTrue(self.connection.data_is_stale)

    async def test_recovery_after_a_long_outage(self) -> None:
        self.clock.advance(10 * ON_DEMAND)
        self.assertTrue(self.connection.data_is_stale)

        await self.poll_to_completion()

        self.assertFalse(self.connection.data_is_stale)

    async def test_last_data_update_is_a_utc_timestamp(self) -> None:
        await self.poll_to_completion()

        stamp = self.connection.last_data_update

        self.assertIsNotNone(stamp.tzinfo)
        self.assertEqual(stamp.utcoffset(), timezone.utc.utcoffset(stamp))


def _silent() -> FakeValveClient:
    client = FakeValveClient()
    client.answer = False
    return client


class FreshnessListenerTests(ConnectionTestCase):
    """Listeners hear about transitions, once each, and never block each other."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.events: list[bool] = []
        self.connection.add_freshness_listener(self.events.append)

    async def test_turning_stale_notifies_once(self) -> None:
        self.clock.advance(ON_DEMAND + 1)

        self.connection.watchdog_check()
        self.connection.watchdog_check()
        self.connection.watchdog_check()

        self.assertEqual(self.events, [True])

    async def test_recovery_notifies_once(self) -> None:
        self.clock.advance(ON_DEMAND + 1)
        self.connection.watchdog_check()

        await self.poll_to_completion()

        self.assertEqual(self.events, [True, False])

    async def test_repeated_stale_and_recovery_cycles(self) -> None:
        for _ in range(4):
            self.clock.advance(ON_DEMAND + 1)
            self.connection.watchdog_check()
            self.connection._next_connection_time = None
            await self.poll_to_completion()

        self.assertEqual(self.events, [True, False] * 4)

    async def test_no_notification_while_fresh(self) -> None:
        await self.poll_to_completion()
        for _ in range(10):
            self.clock.advance(60)
            self.connection.watchdog_check()

        self.assertEqual(self.events, [])

    async def test_enabling_persistent_mode_can_make_old_data_stale_at_once(self) -> None:
        await self.poll_to_completion()
        self.clock.advance(PERSISTENT_MIN + 30)  # fine on demand, stale persistent
        self.assertFalse(self.connection.data_is_stale)

        await self.connection.async_set_persistent_connection_enabled(True)

        self.assertEqual(self.events, [True])

    async def test_disabling_persistent_mode_can_make_data_fresh_again(self) -> None:
        await self.poll_to_completion()
        await self.connection.async_set_persistent_connection_enabled(True)
        self.clock.advance(PERSISTENT_MIN + 30)
        self.connection.watchdog_check()
        self.assertEqual(self.events, [True])

        await self.connection.async_set_persistent_connection_enabled(False)

        self.assertEqual(self.events, [True, False])

    async def test_changing_the_interval_re_evaluates_staleness(self) -> None:
        await self.poll_to_completion()
        await self.connection.async_set_persistent_connection_enabled(True)
        await self.connection.async_set_persistent_poll_interval(300)
        self.clock.advance(PERSISTENT_MIN + 30)
        self.connection.watchdog_check()
        self.assertEqual(self.events, [])  # 300 s interval allows 2400 s

        await self.connection.async_set_persistent_poll_interval(5)

        self.assertEqual(self.events, [True])

    async def test_a_failing_listener_does_not_block_the_others(self) -> None:
        def broken(stale: bool) -> None:
            raise RuntimeError("listener bug")

        later = []
        self.connection.add_freshness_listener(broken)
        self.connection.add_freshness_listener(later.append)
        self.clock.advance(ON_DEMAND + 1)

        self.connection.watchdog_check()

        self.assertEqual(self.events, [True])
        self.assertEqual(later, [True])
        self.assertTrue(self.log.contains("freshness listener"))

    async def test_removed_listener_hears_nothing(self) -> None:
        heard = []
        remove = self.connection.add_freshness_listener(heard.append)
        remove()
        remove()  # removing twice is harmless
        self.clock.advance(ON_DEMAND + 1)

        self.connection.watchdog_check()

        self.assertEqual(heard, [])

    async def test_listener_may_remove_itself_during_notification(self) -> None:
        holder = []
        calls = []

        def once(stale: bool) -> None:
            calls.append(stale)
            holder[0]()

        holder.append(self.connection.add_freshness_listener(once))
        self.clock.advance(ON_DEMAND + 1)
        self.connection.watchdog_check()
        await self.poll_to_completion()

        self.assertEqual(calls, [True])

    async def test_stale_transition_is_logged_loudly(self) -> None:
        self.clock.advance(ON_DEMAND + 1)

        self.connection.watchdog_check()

        self.assertTrue(self.log.contains("sensors are now unavailable"))

    async def test_dashboard_listeners_still_receive_data(self) -> None:
        received = Mock()
        self.connection.add_dashboard_listener(received)

        await self.poll_to_completion()

        received.assert_called_once()
        self.assertEqual(received.call_args.args[0].water_usage, 567)
