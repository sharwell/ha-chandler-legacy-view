"""Entities must never present stale data as live, and must keep their settings.

For leak detection, a flow sensor that quietly keeps its last value after
polling stops is worse than no sensor. These tests drive real entities from a
real connection and check availability, the freshness entities, the platform
wiring, and restoration of the persistent-connection settings.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
import logging
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from harness import FakeClock, dashboard_frames
from test_availability_platforms import DiscoveryDispatcher, production

connection_module = production.connection
ON_DEMAND = connection_module.STALE_AFTER_ON_DEMAND_SECONDS
DEFAULT_INTERVAL = connection_module.DEFAULT_PERSISTENT_POLL_INTERVAL_SECONDS


def make_advertisement(address: str = "valve", name: str = "CS_Meter_Soft"):
    return connection_module.ValveAdvertisement(
        address=address, name=name, rssi=-50, manufacturer_data={}, service_data={},
        model="Evb019", valve_type="MeteredSoftener", authentication_required=True,
    )


class EntityCase(unittest.IsolatedAsyncioTestCase):
    """A real connection with a fake clock and no Bluetooth I/O."""

    async def asyncSetUp(self) -> None:
        self.clock = FakeClock()
        self.enterContext(patch.object(
            connection_module.ValveConnection, "_monotonic", staticmethod(self.clock)
        ))
        self.hass = SimpleNamespace(
            loop=asyncio.get_running_loop(),
            state=connection_module.CoreState.running,
            async_create_task=Mock(side_effect=lambda coroutine: coroutine.close()),
        )
        # Stale-data warnings are expected here; keep the test output readable.
        logger = logging.getLogger(connection_module.__name__)
        previous = logger.level
        logger.setLevel(logging.ERROR)
        self.addCleanup(logger.setLevel, previous)
        self.advertisement = make_advertisement()
        self.connection = connection_module.ValveConnection(
            self.hass, "valve", "entry"
        )
        self.connection.update_from_advertisement(self.advertisement)
        self.addAsyncCleanup(self.connection.async_unload)

    def receive_dashboard(self, usage: int = 567) -> None:
        self.connection._handle_dashboard_packets(dashboard_frames(usage=usage))

    def go_stale(self) -> None:
        self.clock.advance(ON_DEMAND + 1)
        self.connection.watchdog_check()

    async def attach(self, entity):
        """Register an entity the way Home Assistant does."""

        entity.hass = self.hass
        await entity.async_added_to_hass()
        self.addAsyncCleanup(entity.async_will_remove_from_hass)
        return entity


class StaleSensorTests(EntityCase):
    """A dashboard sensor reports unavailable instead of an old reading."""

    SENSOR_TYPES = (
        "ValvePresentFlowSensor",
        "ValveWaterUsageTodaySensor",
        "ValvePeakFlowTodaySensor",
        "ValveBatteryCapacitySensor",
        "ValveSoftWaterRemainingSensor",
        "ValveWaterHardnessSensor",
        "ValveTimeOfDaySensor",
    )

    def make(self, name: str):
        return getattr(production.sensor, name)(self.advertisement, self.connection)

    async def test_fresh_sensor_is_available_with_its_value(self) -> None:
        sensor = await self.attach(self.make("ValveWaterUsageTodaySensor"))

        self.receive_dashboard(usage=321)

        self.assertTrue(sensor.available)
        self.assertEqual(sensor._attr_native_value, 321)

    async def test_every_dashboard_sensor_goes_unavailable_when_stale(self) -> None:
        for name in self.SENSOR_TYPES:
            with self.subTest(sensor=name):
                sensor = await self.attach(self.make(name))
                self.assertTrue(sensor.available)

                self.go_stale()

                self.assertFalse(sensor.available)
                self.connection._last_dashboard_monotonic = self.clock.now
                self.connection.watchdog_check()
                self.assertTrue(sensor.available)

    async def test_state_is_republished_when_data_turns_stale(self) -> None:
        sensor = await self.attach(self.make("ValvePresentFlowSensor"))
        before = getattr(sensor, "state_writes", 0)

        self.go_stale()

        self.assertEqual(sensor.state_writes, before + 1)

    async def test_state_is_republished_when_data_recovers(self) -> None:
        sensor = await self.attach(self.make("ValvePresentFlowSensor"))
        self.go_stale()
        before = sensor.state_writes

        self.receive_dashboard()

        self.assertGreater(sensor.state_writes, before)
        self.assertTrue(sensor.available)

    async def test_recovered_sensor_shows_the_new_value_not_the_old_one(self) -> None:
        sensor = await self.attach(self.make("ValveWaterUsageTodaySensor"))
        self.receive_dashboard(usage=100)
        self.go_stale()

        self.receive_dashboard(usage=250)

        self.assertTrue(sensor.available)
        self.assertEqual(sensor._attr_native_value, 250)

    async def test_out_of_range_valve_is_unavailable_even_with_fresh_data(self) -> None:
        sensor = await self.attach(self.make("ValvePresentFlowSensor"))
        self.receive_dashboard()

        sensor.async_handle_bluetooth_update(
            self.advertisement, production.discovery.ValveDiscoveryChange.UNAVAILABLE
        )

        self.assertFalse(sensor.available)

    async def test_returning_valve_with_stale_data_stays_unavailable(self) -> None:
        sensor = await self.attach(self.make("ValvePresentFlowSensor"))
        self.go_stale()

        sensor.async_handle_bluetooth_update(
            self.advertisement, production.discovery.ValveDiscoveryChange.AVAILABLE
        )

        self.assertFalse(sensor.available)

    async def test_removed_sensor_stops_listening(self) -> None:
        sensor = await self.attach(self.make("ValvePresentFlowSensor"))
        await sensor.async_will_remove_from_hass()
        before = sensor.state_writes if hasattr(sensor, "state_writes") else 0

        self.go_stale()

        self.assertEqual(getattr(sensor, "state_writes", 0), before)
        self.assertEqual(self.connection._freshness_listeners, [])

    async def test_never_connected_valve_becomes_unavailable_eventually(self) -> None:
        sensor = await self.attach(self.make("ValvePresentFlowSensor"))
        self.assertTrue(sensor.available)

        self.go_stale()

        self.assertFalse(sensor.available)


class LastDataUpdateSensorTests(EntityCase):
    """The data-age gauge outlives the data it describes."""

    async def make(self):
        return await self.attach(
            production.sensor.ValveLastDataUpdateSensor(
                self.advertisement, self.connection
            )
        )

    async def test_is_always_available_even_when_stale(self) -> None:
        sensor = await self.make()

        self.go_stale()

        self.assertTrue(sensor.available)

    async def test_says_there_is_no_data_until_some_arrives(self) -> None:
        sensor = await self.make()
        self.assertEqual(sensor._attr_native_value, production.sensor.DATA_AGE_NONE)

        self.receive_dashboard()

        self.assertEqual(sensor._attr_native_value, production.sensor.DATA_AGE_FRESH)

    async def test_keeps_the_exact_time_while_everything_else_is_stale(self) -> None:
        sensor = await self.make()
        self.receive_dashboard()
        self.assertIsNone(sensor.extra_state_attributes)
        arrived = self.connection.last_data_update.isoformat()

        self.go_stale()

        self.assertEqual(sensor._attr_native_value, "15 to 60 minutes ago")
        self.assertEqual(sensor.extra_state_attributes, {"last_data_update": arrived})

    async def test_is_a_diagnostic_enum(self) -> None:
        sensor = await self.make()

        self.assertEqual(sensor._attr_device_class, "enum")
        self.assertEqual(sensor._attr_entity_category, "diagnostic")
        self.assertEqual(sensor._attr_unique_id, "valve_last_data_update")
        self.assertTrue(sensor._attr_name.endswith("Last Data Update"))


class DataStaleBinarySensorTests(EntityCase):
    """The sensor to alert on."""

    async def make(self):
        return await self.attach(
            production.binary_sensor.ValveDataStaleBinarySensor(
                self.advertisement, self.connection
            )
        )

    async def test_starts_off_for_a_freshly_seen_valve(self) -> None:
        sensor = await self.make()

        self.assertFalse(sensor._attr_is_on)

    async def test_turns_on_when_data_goes_stale_and_off_on_recovery(self) -> None:
        sensor = await self.make()

        self.go_stale()
        self.assertTrue(sensor._attr_is_on)

        self.receive_dashboard()
        self.assertFalse(sensor._attr_is_on)

    async def test_publishes_every_transition(self) -> None:
        sensor = await self.make()

        self.go_stale()
        self.receive_dashboard()

        self.assertEqual(sensor.state_writes, 2)

    async def test_is_available_even_when_the_valve_is_gone(self) -> None:
        sensor = await self.make()

        sensor.async_handle_bluetooth_update(
            self.advertisement, production.discovery.ValveDiscoveryChange.UNAVAILABLE
        )

        self.assertTrue(sensor.available)

    async def test_valve_loss_for_a_long_time_reads_as_a_problem(self) -> None:
        sensor = await self.make()
        sensor.async_handle_bluetooth_update(
            self.advertisement, production.discovery.ValveDiscoveryChange.UNAVAILABLE
        )

        self.go_stale()

        self.assertTrue(sensor.available)
        self.assertTrue(sensor._attr_is_on)

    async def test_is_a_diagnostic_problem_sensor(self) -> None:
        sensor = await self.make()

        self.assertEqual(sensor._attr_device_class, "problem")
        self.assertEqual(sensor._attr_entity_category, "diagnostic")
        self.assertEqual(sensor._attr_unique_id, "valve_data_stale")

    async def test_name_is_not_duplicated_by_repeated_advertisements(self) -> None:
        sensor = await self.make()

        for rssi in (-50, -55, -60):
            sensor.async_handle_bluetooth_update(
                replace(self.advertisement, rssi=rssi),
                production.discovery.ValveDiscoveryChange.AVAILABLE,
            )

        self.assertEqual(sensor._attr_name.count("Data Stale"), 1)

    async def test_attributes_explain_the_limit_and_do_not_churn(self) -> None:
        sensor = await self.make()
        self.assertEqual(
            sensor.extra_state_attributes, {"stale_after_seconds": ON_DEMAND}
        )

        self.receive_dashboard()

        # When data last arrived is the Last Data Update sensor. Repeating it here
        # changed the attributes on every update and wrote a recorder row each time.
        self.assertEqual(
            sensor.extra_state_attributes, {"stale_after_seconds": ON_DEMAND}
        )

    async def test_attribute_follows_persistent_mode(self) -> None:
        sensor = await self.make()

        await self.connection.async_set_persistent_connection_enabled(True)
        await self.connection.async_set_persistent_poll_interval(5)

        self.assertEqual(
            sensor.extra_state_attributes["stale_after_seconds"],
            connection_module.STALE_AFTER_PERSISTENT_MIN_SECONDS,
        )

    async def test_removed_sensor_stops_listening(self) -> None:
        sensor = await self.make()

        await sensor.async_will_remove_from_hass()

        self.assertEqual(self.connection._freshness_listeners, [])


class PlatformWiringTests(unittest.IsolatedAsyncioTestCase):
    """The platforms create the freshness entities once per valve."""

    async def build(self, *, with_connections: bool = True):
        softener = make_advertisement("softener", "CS_Meter_Soft")
        water_filter = make_advertisement("filter", "C2_04")
        discovery = DiscoveryDispatcher((softener, water_filter))
        entry = SimpleNamespace(
            entry_id="e", options={}, data={}, async_on_unload=Mock()
        )
        hass = SimpleNamespace(
            loop=asyncio.get_running_loop(),
            state=connection_module.CoreState.running,
            async_create_task=Mock(side_effect=lambda coroutine: coroutine.close()),
        )
        manager = connection_module.ValveConnectionManager(hass, entry, discovery)
        self.addAsyncCleanup(manager.async_unload)
        await manager.async_setup()
        data = {
            production.number.DATA_DISCOVERY_MANAGER: discovery,
            production.number.DATA_DISCOVERY_DEVICE_ID: "parent",
        }
        if with_connections:
            data[production.number.DATA_CONNECTION_MANAGER] = manager
        hass.data = {production.number.DOMAIN: {entry.entry_id: data}}
        return hass, entry, discovery, manager

    async def test_each_valve_gets_one_data_stale_sensor(self) -> None:
        hass, entry, _, _ = await self.build()
        added = []

        await production.binary_sensor.async_setup_entry(hass, entry, added.extend)

        stale = [
            entity for entity in added
            if isinstance(entity, production.binary_sensor.ValveDataStaleBinarySensor)
        ]
        self.assertEqual(
            sorted(entity._attr_unique_id for entity in stale),
            ["filter_data_stale", "softener_data_stale"],
        )

    async def test_repeated_discovery_does_not_duplicate_the_sensor(self) -> None:
        hass, entry, discovery, _ = await self.build()
        added = []
        await production.binary_sensor.async_setup_entry(hass, entry, added.extend)
        count = len(added)

        for _ in range(3):
            discovery.emit(
                make_advertisement("softener"),
                production.discovery.ValveDiscoveryChange.AVAILABLE,
            )

        self.assertEqual(len(added), count)

    async def test_platform_still_works_without_a_connection_manager(self) -> None:
        hass, entry, _, _ = await self.build(with_connections=False)
        added = []

        await production.binary_sensor.async_setup_entry(hass, entry, added.extend)

        self.assertFalse(any(
            isinstance(entity, production.binary_sensor.ValveDataStaleBinarySensor)
            for entity in added
        ))
        self.assertTrue(any(
            isinstance(entity, production.binary_sensor.ValvePresenceBinarySensor)
            for entity in added
        ))

    async def test_each_valve_gets_one_last_update_sensor(self) -> None:
        hass, entry, _, _ = await self.build()
        added = []

        await production.sensor.async_setup_entry(hass, entry, added.extend)

        last_update = [
            entity for entity in added
            if isinstance(entity, production.sensor.ValveLastDataUpdateSensor)
        ]
        self.assertEqual(
            sorted(entity._attr_unique_id for entity in last_update),
            ["filter_last_data_update", "softener_last_data_update"],
        )


class RestoreCase(EntityCase):
    """Settings come back after a restart or reload."""

    def last_state(self, state: str):
        return SimpleNamespace(state=state)


class SwitchRestoreTests(RestoreCase):
    async def restore(self, state):
        switch = production.switch.ValvePersistentConnectionSwitch(
            self.advertisement, self.connection
        )
        switch.last_state = None if state is None else self.last_state(state)
        await self.attach(switch)
        return switch

    async def test_on_is_restored_and_requested_from_the_connection(self) -> None:
        switch = await self.restore("on")

        self.assertTrue(self.connection.persistent_connection_enabled)
        self.assertTrue(switch._attr_is_on)

    async def test_restoring_on_starts_a_poll_to_open_the_session(self) -> None:
        await self.restore("on")

        self.hass.async_create_task.assert_called_once()

    async def test_each_non_on_state_stays_off(self) -> None:
        for state in ("off", "unknown", "unavailable", None):
            with self.subTest(state=state):
                connection = connection_module.ValveConnection(
                    self.hass, "valve", "entry"
                )
                connection.update_from_advertisement(self.advertisement)
                switch = production.switch.ValvePersistentConnectionSwitch(
                    self.advertisement, connection
                )
                switch.last_state = None if state is None else self.last_state(state)
                switch.hass = self.hass

                await switch.async_added_to_hass()

                self.assertFalse(connection.persistent_connection_enabled)
                self.assertFalse(switch._attr_is_on)
                await connection.async_unload()

    async def test_already_enabled_connection_is_not_toggled_again(self) -> None:
        self.connection.async_set_persistent_connection_enabled = AsyncMock()
        self.connection._persistent_connection_enabled = True
        switch = production.switch.ValvePersistentConnectionSwitch(
            self.advertisement, self.connection
        )
        switch.last_state = self.last_state("on")
        switch.hass = self.hass

        await switch.async_added_to_hass()

        self.connection.async_set_persistent_connection_enabled.assert_not_awaited()

    async def test_user_toggle_still_works_after_restore(self) -> None:
        switch = await self.restore("on")

        await switch.async_turn_off()

        self.assertFalse(self.connection.persistent_connection_enabled)
        self.assertFalse(switch._attr_is_on)


class NumberRestoreTests(RestoreCase):
    async def restore(self, state):
        number = production.number.ValvePersistentPollIntervalNumber(
            self.advertisement, self.connection
        )
        number.last_state = None if state is None else self.last_state(state)
        await self.attach(number)
        return number

    async def test_a_saved_interval_is_restored(self) -> None:
        for saved, expected in (("5.0", 5.0), ("6", 6.0), ("45.5", 45.5), ("300", 300.0)):
            with self.subTest(saved=saved):
                number = await self.restore(saved)

                self.assertEqual(self.connection.persistent_poll_interval, expected)
                self.assertEqual(number._attr_native_value, expected)

    async def test_unusable_states_keep_the_default(self) -> None:
        for saved in (None, "unknown", "unavailable", "", "abc"):
            with self.subTest(saved=saved):
                connection = connection_module.ValveConnection(
                    self.hass, "valve", "entry"
                )
                number = production.number.ValvePersistentPollIntervalNumber(
                    self.advertisement, connection
                )
                number.last_state = None if saved is None else self.last_state(saved)
                number.hass = self.hass

                await number.async_added_to_hass()

                self.assertEqual(connection.persistent_poll_interval, DEFAULT_INTERVAL)
                self.assertEqual(number._attr_native_value, DEFAULT_INTERVAL)
                await connection.async_unload()

    async def test_out_of_range_values_are_clamped_not_trusted(self) -> None:
        for saved, expected in (
            ("0", connection_module.MIN_PERSISTENT_POLL_INTERVAL_SECONDS),
            ("-3", connection_module.MIN_PERSISTENT_POLL_INTERVAL_SECONDS),
            ("9999", connection_module.MAX_PERSISTENT_POLL_INTERVAL_SECONDS),
            ("nan", connection_module.MIN_PERSISTENT_POLL_INTERVAL_SECONDS),
            ("inf", connection_module.MAX_PERSISTENT_POLL_INTERVAL_SECONDS),
        ):
            with self.subTest(saved=saved):
                await self.restore(saved)

                self.assertEqual(self.connection.persistent_poll_interval, expected)

    async def test_user_changes_still_work_after_restore(self) -> None:
        number = await self.restore("6")

        await number.async_set_native_value(12)

        self.assertEqual(self.connection.persistent_poll_interval, 12.0)
        self.assertEqual(number._attr_native_value, 12.0)

    async def test_restored_interval_sets_the_stale_limit(self) -> None:
        await self.connection.async_set_persistent_connection_enabled(True)

        await self.restore("300")

        self.assertEqual(self.connection.stale_after_seconds, 8 * 300)
