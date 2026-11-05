"""The freshness entities must not flood the recorder.

With a persistent connection a dashboard arrives every few seconds. A timestamp
sensor that publishes each one became the busiest entity in a home's database.
Last Data Update now reports the age of the data in coarse steps ("Within 5
minutes", "15 to 60 minutes ago", ...) and only changes when a step is crossed,
so a healthy valve writes almost nothing. Staleness is judged from the
connection's own timing, so the coarse gauge never hides a stale valve.
"""

from __future__ import annotations

from test_entity_reliability import EntityCase, connection_module, production

TIERS = production.sensor.DATA_AGE_TIERS
FRESH = production.sensor.DATA_AGE_FRESH
OLDEST = production.sensor.DATA_AGE_OLDEST
NONE = production.sensor.DATA_AGE_NONE


class Published(list):
    """What reached the recorder; ``clear()`` forgets rows but not the last state."""

    last = None
    calls = 0  # every write, including ones Home Assistant would ignore


def recording(entity):
    """Collect what the entity publishes, as the recorder would see it.

    Home Assistant ignores a write that changes neither state nor attributes, so
    consecutive identical publications count once.
    """

    seen = Published()

    def write() -> None:
        seen.calls += 1
        item = (
            entity._attr_native_value if hasattr(entity, "_attr_native_value")
            else entity._attr_is_on,
            entity.extra_state_attributes,
        )
        if item != seen.last:
            seen.last = item
            seen.append(item)

    entity.async_write_ha_state = write
    return seen


class DataAgeLabelTests(EntityCase):
    async def test_no_data_yet(self) -> None:
        self.assertEqual(self.connection.data_age_label, NONE)

    async def test_fresh_data(self) -> None:
        self.receive_dashboard()

        self.assertEqual(self.connection.data_age_label, FRESH)

    async def test_every_step_starts_exactly_at_its_limit(self) -> None:
        labels = [label for _, label in TIERS] + [OLDEST]
        for index, (limit, _) in enumerate(TIERS):
            with self.subTest(limit=limit):
                self.receive_dashboard()
                self.clock.advance(limit - 1)
                self.assertEqual(self.connection.data_age_label, labels[index])
                self.clock.advance(1)
                self.assertEqual(self.connection.data_age_label, labels[index + 1])

    async def test_new_data_makes_it_fresh_again(self) -> None:
        self.receive_dashboard()
        self.clock.advance(5 * 24 * 3600)
        self.assertEqual(self.connection.data_age_label, OLDEST)

        self.receive_dashboard()

        self.assertEqual(self.connection.data_age_label, FRESH)


class GaugeSensorTests(EntityCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.sensor = await self.attach(production.sensor.ValveLastDataUpdateSensor(
            self.advertisement, self.connection
        ))
        self.published = recording(self.sensor)

    def tick(self, seconds: float) -> None:
        self.clock.advance(seconds)
        self.connection.watchdog_check()

    async def test_a_healthy_stream_publishes_exactly_once(self) -> None:
        for _ in range(500):  # about thirty minutes of data every 3.5 seconds
            self.receive_dashboard()
            self.tick(3.5)

        self.assertEqual([value for value, _ in self.published], [FRESH])

    async def test_a_healthy_stream_does_not_even_attempt_more_writes(self) -> None:
        for _ in range(500):
            self.receive_dashboard()
            self.tick(3.5)

        self.assertEqual(self.published.calls, 1)

    async def test_it_publishes_when_a_step_is_crossed_and_not_before(self) -> None:
        self.receive_dashboard()
        self.published.clear()

        self.tick(TIERS[0][0] - 1)
        self.assertEqual(self.published, [])

        self.tick(1)
        self.assertEqual([value for value, _ in self.published], ["5 to 15 minutes ago"])

    async def test_a_long_outage_writes_once_per_step(self) -> None:
        self.receive_dashboard()
        self.published.clear()

        for _ in range(26 * 60):  # twenty-six hours, checked every minute
            self.tick(60)

        self.assertEqual(
            [value for value, _ in self.published],
            [label for _, label in TIERS[1:]] + [OLDEST],
        )

    async def test_the_exact_time_appears_only_once_data_is_no_longer_fresh(self) -> None:
        self.receive_dashboard()
        arrived = self.connection.last_data_update.isoformat()
        self.published.clear()

        self.tick(TIERS[0][0])

        self.assertEqual(self.published[-1][1], {"last_data_update": arrived})

    async def test_recovery_publishes_fresh_at_once_without_the_old_time(self) -> None:
        self.receive_dashboard()
        self.tick(7200)
        self.published.clear()

        self.receive_dashboard()

        self.assertEqual(self.published, [(FRESH, None)])

    async def test_it_stays_available_through_the_whole_outage(self) -> None:
        self.receive_dashboard()
        for _ in range(60):
            self.tick(3600)
            self.assertTrue(self.sensor.available)

    async def test_every_published_value_is_a_listed_option(self) -> None:
        self.receive_dashboard()
        for _ in range(26 * 60):
            self.tick(60)

        options = set(self.sensor._attr_options)
        self.assertTrue({value for value, _ in self.published} <= options)
        self.assertIn(NONE, options)

    async def test_it_never_writes_for_a_stream_even_through_a_stale_cycle(self) -> None:
        # One burst of data, a long silence, then data again: four publications.
        self.receive_dashboard()
        self.tick(400)
        self.tick(1000)
        self.receive_dashboard()

        self.assertEqual(
            [value for value, _ in self.published],
            [FRESH, "5 to 15 minutes ago", "15 to 60 minutes ago", FRESH],
        )


class DataStaleChurnTests(EntityCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.sensor = await self.attach(production.binary_sensor.ValveDataStaleBinarySensor(
            self.advertisement, self.connection
        ))
        self.published = recording(self.sensor)

    async def test_data_arriving_does_not_change_the_attributes(self) -> None:
        before = dict(self.sensor.extra_state_attributes)

        for usage in (100, 101, 102):
            self.receive_dashboard(usage=usage)

        self.assertEqual(dict(self.sensor.extra_state_attributes), before)
        self.assertNotIn("last_data_update", self.sensor.extra_state_attributes)

    async def test_a_steady_stream_publishes_nothing_new(self) -> None:
        for _ in range(300):
            self.receive_dashboard()
            self.clock.advance(3.5)
            self.connection.watchdog_check()

        self.assertLessEqual(len(self.published), 1)

    async def test_the_shown_limit_follows_the_polling_mode(self) -> None:
        self.assertEqual(
            self.sensor.extra_state_attributes["stale_after_seconds"],
            connection_module.STALE_AFTER_ON_DEMAND_SECONDS,
        )

        await self.connection.async_set_persistent_connection_enabled(True)

        self.assertEqual(
            self.sensor.extra_state_attributes["stale_after_seconds"],
            connection_module.STALE_AFTER_PERSISTENT_MIN_SECONDS,
        )
        self.assertEqual(
            self.published[-1][1],
            {"stale_after_seconds": connection_module.STALE_AFTER_PERSISTENT_MIN_SECONDS},
        )

    async def test_the_shown_limit_follows_the_interval(self) -> None:
        await self.connection.async_set_persistent_connection_enabled(True)
        await self.connection.async_set_persistent_poll_interval(60)

        self.assertEqual(
            self.sensor.extra_state_attributes["stale_after_seconds"],
            connection_module.STALE_AFTER_PERSISTENT_INTERVALS * 60,
        )
        self.assertEqual(
            self.published[-1][1],
            {"stale_after_seconds": connection_module.STALE_AFTER_PERSISTENT_INTERVALS * 60},
        )

    async def test_removed_sensor_stops_listening(self) -> None:
        await self.sensor.async_will_remove_from_hass()

        self.assertEqual(self.connection._age_listeners, [])


class AgeListenerTests(EntityCase):
    async def test_listeners_hear_only_about_changes(self) -> None:
        heard = []
        self.connection.add_age_listener(lambda: heard.append(1))

        self.receive_dashboard()
        count = len(heard)
        for _ in range(100):
            self.receive_dashboard()
            self.clock.advance(3.5)
            self.connection.watchdog_check()

        self.assertEqual(len(heard), count)

    async def test_a_removed_listener_is_not_called(self) -> None:
        heard = []
        remove = self.connection.add_age_listener(lambda: heard.append(1))

        remove()
        remove()
        self.receive_dashboard()

        self.assertEqual(heard, [])

    async def test_a_failing_listener_does_not_block_the_others(self) -> None:
        later = []

        def boom() -> None:
            raise RuntimeError

        self.connection.add_age_listener(boom)
        self.connection.add_age_listener(lambda: later.append(1))

        with self.assertLogs(connection_module.__name__, level="ERROR"):
            self.receive_dashboard()

        self.assertEqual(later, [1])
