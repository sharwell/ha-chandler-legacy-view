"""Exercise availability fanout through real connection and platform listeners."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from enum import Enum
import importlib
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch


def _load_platform_modules() -> SimpleNamespace:
    """Load production listeners with only Home Assistant and BLE services stubbed."""

    modules: dict[str, ModuleType] = {}

    def module(name: str, **attributes) -> ModuleType:
        if name not in modules:
            modules[name] = ModuleType(name)
            if "." in name:
                parent, _, child = name.rpartition(".")
                setattr(module(parent), child, modules[name])
        vars(modules[name]).update(attributes)
        return modules[name]

    class Entity:
        hass = None

        def async_write_ha_state(self) -> None:
            self.state_writes = getattr(self, "state_writes", 0) + 1

    module("bleak.backends.client", BaseBleakClient=object)
    module(
        "bleak_retry_connector",
        BLEAK_RETRY_EXCEPTIONS=(OSError,),
        BleakClientWithServiceCache=object,
        establish_connection=AsyncMock(),
    )
    module(
        "homeassistant.components.bluetooth",
        BluetoothChange=Enum("BluetoothChange", "ADVERTISEMENT"),
        BluetoothScanningMode=SimpleNamespace(PASSIVE="passive"),
        BluetoothServiceInfoBleak=object,
        async_register_callback=Mock(),
        async_track_unavailable=Mock(),
        async_ble_device_from_address=Mock(return_value=object()),
    )
    module("homeassistant.config_entries", ConfigEntry=object)
    module(
        "homeassistant.const",
        EVENT_HOMEASSISTANT_STARTED="homeassistant_started",
        PERCENTAGE="%",
        Platform=SimpleNamespace(
            BINARY_SENSOR="binary_sensor", NUMBER="number", SENSOR="sensor",
            SWITCH="switch",
        ),
        UnitOfTime=SimpleNamespace(SECONDS="s", DAYS="d"),
        UnitOfVolume=SimpleNamespace(GALLONS="gal"),
        UnitOfVolumeFlowRate=SimpleNamespace(GALLONS_PER_MINUTE="gal/min"),
    )
    module(
        "homeassistant.core",
        CALLBACK_TYPE=object,
        CoreState=SimpleNamespace(running="running"),
        HomeAssistant=object,
        callback=lambda function: function,
    )
    module(
        "homeassistant.helpers.entity",
        DeviceInfo=dict,
        Entity=Entity,
        EntityCategory=SimpleNamespace(CONFIG="config"),
    )
    module("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
    module(
        "homeassistant.helpers.event",
        async_call_later=Mock(),
        async_track_time_interval=Mock(return_value=Mock()),
    )
    module("homeassistant.util.dt", utcnow=lambda: datetime.now(timezone.utc))
    for platform, entity_name in (
        ("binary_sensor", "BinarySensorEntity"),
        ("sensor", "SensorEntity"),
        ("number", "NumberEntity"),
        ("switch", "SwitchEntity"),
    ):
        module(
            f"homeassistant.components.{platform}",
            **{entity_name: type(entity_name, (Entity,), {})},
        )
    module(
        "homeassistant.components.binary_sensor",
        BinarySensorDeviceClass=SimpleNamespace(PRESENCE="presence", PROBLEM="problem"),
    )
    module(
        "homeassistant.components.sensor",
        SensorDeviceClass=SimpleNamespace(TIMESTAMP="timestamp", BATTERY="battery"),
        SensorStateClass=SimpleNamespace(MEASUREMENT="measurement"),
    )

    package = "_chandler_availability_platform_tests"
    component_path = (
        Path(__file__).resolve().parents[1] / "custom_components/chandler_legacy_view"
    )
    module(package, __path__=[str(component_path)])
    module(
        f"{package}.device_registry",
        async_update_device_serial_number=Mock(),
        async_update_device_sw_version=Mock(),
    )
    with patch.dict(sys.modules, modules):
        return SimpleNamespace(**{
            name: importlib.import_module(f"{package}.{name}")
            for name in (
                "discovery", "connection", "binary_sensor", "sensor", "number", "switch",
            )
        })


production = _load_platform_modules()


class DiscoveryDispatcher:
    """Supply discovery events to production listeners in registration order."""

    def __init__(self, advertisements) -> None:
        self.devices = {item.address: item for item in advertisements}
        self.listeners = []
        self.async_set_connection_state = Mock()

    def async_add_listener(self, listener):
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener)

    def emit(self, advertisement, change) -> None:
        if change is production.discovery.ValveDiscoveryChange.UNAVAILABLE:
            self.devices.pop(advertisement.address, None)
        else:
            self.devices[advertisement.address] = advertisement
        for listener in list(self.listeners):
            listener(advertisement, change)


class AvailabilityPlatformTests(unittest.IsolatedAsyncioTestCase):
    """A lost valve must stop polling and update every existing entity."""

    async def exercise_loss_and_recovery(self, *, already_discovered: bool) -> None:
        advertisement_type = production.connection.ValveAdvertisement
        softener = advertisement_type(
            address="softener", name="CS_Meter_Soft", rssi=-50,
            manufacturer_data={}, service_data={}, model="Evb019",
            valve_type="MeteredSoftener", authentication_required=True,
        )
        water_filter = advertisement_type(
            address="filter", name="C2_04", rssi=-60,
            manufacturer_data={}, service_data={}, model="Evb019",
            valve_type="BackwashingFilter", authentication_required=True,
        )
        advertisements = (softener, water_filter)
        discovery = DiscoveryDispatcher(advertisements if already_discovered else ())
        unload_callbacks = []
        entry = SimpleNamespace(
            entry_id="test", options={}, data={},
            async_on_unload=unload_callbacks.append,
        )
        # Close scheduled coroutines so no BLE I/O occurs, while preserving the
        # real schedule_poll availability gate and observable scheduling behavior.
        hass = SimpleNamespace(
            loop=asyncio.get_running_loop(),
            state=production.connection.CoreState.running,
            async_create_task=Mock(side_effect=lambda coroutine: coroutine.close()),
        )
        manager = production.connection.ValveConnectionManager(hass, entry, discovery)
        self.addAsyncCleanup(manager.async_unload)
        await manager.async_setup()
        constants = production.number
        hass.data = {
            constants.DOMAIN: {
                entry.entry_id: {
                    constants.DATA_DISCOVERY_MANAGER: discovery,
                    constants.DATA_CONNECTION_MANAGER: manager,
                    constants.DATA_DISCOVERY_DEVICE_ID: "registered-parent-id",
                }
            }
        }
        entities_by_platform = {}
        for name in ("binary_sensor", "sensor", "number", "switch"):
            entities = []
            entities_by_platform[name] = entities

            def add_entities(created, entities=entities) -> None:
                for entity in created:
                    # HA reads device_info while adding entities, before they
                    # are attached. Initial and later discoveries need the ID.
                    self.assertEqual(
                        entity.device_info["via_device_id"], "registered-parent-id"
                    )
                    self.assertNotIn("via_device", entity.device_info)
                    self.assertEqual(
                        entity.device_info["identifiers"],
                        {(constants.DOMAIN, entity._advertisement.address)},
                    )
                    entity.hass = hass
                entities.extend(created)

            await getattr(production, name).async_setup_entry(hass, entry, add_entities)

        change = production.discovery.ValveDiscoveryChange
        if not already_discovered:
            self.assertTrue(all(not entities for entities in entities_by_platform.values()))
            for advertisement in advertisements:
                discovery.emit(advertisement, change.AVAILABLE)

        self.assertEqual(hass.async_create_task.call_count, 2)
        connections = {
            item.address: manager.get_connection(item.address) for item in advertisements
        }
        all_entities = [
            entity for entities in entities_by_platform.values() for entity in entities
        ]
        softener_entities = [
            entity for entity in all_entities if entity._advertisement.address == "softener"
        ]
        filter_entities = [
            entity for entity in all_entities if entity._advertisement.address == "filter"
        ]
        for name, entities in entities_by_platform.items():
            with self.subTest(platform=name):
                self.assertTrue(any(entity in softener_entities for entity in entities))
                self.assertTrue(any(entity in filter_entities for entity in entities))
        self.assertTrue(all(entity._attr_available for entity in all_entities))
        presence = next(
            entity for entity in softener_entities
            if isinstance(entity, production.binary_sensor.ValvePresenceBinarySensor)
        )
        original_ids = tuple(id(entity) for entity in all_entities)
        original_writes = {
            id(entity): getattr(entity, "state_writes", 0) for entity in all_entities
        }

        discovery.emit(softener, change.UNAVAILABLE)

        self.assertFalse(connections["softener"].available)
        self.assertTrue(connections["filter"].available)
        self.assertFalse(presence._attr_is_on)
        self.assertTrue(all(not entity._attr_available for entity in softener_entities))
        self.assertTrue(all(entity._attr_available for entity in filter_entities))
        for entity in softener_entities:
            self.assertEqual(entity.state_writes, original_writes[id(entity)] + 1)
        for entity in filter_entities:
            self.assertEqual(getattr(entity, "state_writes", 0), original_writes[id(entity)])

        # The periodic timer still runs, but it can only schedule the reachable valve.
        manager._handle_poll_interval(datetime.now(timezone.utc))
        self.assertEqual(hass.async_create_task.call_count, 3)

        # A loss notification cannot create a phantom connection or new entities.
        discovery.emit(replace(softener, address="unknown"), change.UNAVAILABLE)
        self.assertIsNone(manager.get_connection("unknown"))
        self.assertEqual(hass.async_create_task.call_count, 3)

        recovered = replace(softener, rssi=-45)
        discovery.emit(recovered, change.AVAILABLE)

        self.assertIs(manager.get_connection("softener"), connections["softener"])
        self.assertTrue(connections["softener"].available)
        self.assertTrue(presence._attr_is_on)
        self.assertTrue(all(entity._attr_available for entity in all_entities))
        self.assertTrue(all(entity._advertisement is recovered for entity in softener_entities))
        for entity in all_entities:
            self.assertEqual(entity.device_info["via_device_id"], "registered-parent-id")
            self.assertNotIn("via_device", entity.device_info)
        self.assertEqual(hass.async_create_task.call_count, 4)
        self.assertEqual(
            tuple(id(entity) for entities in entities_by_platform.values() for entity in entities),
            original_ids,
        )
        for remove_listener in unload_callbacks:
            remove_listener()

    async def test_previously_discovered_valve_loss_and_recovery(self) -> None:
        await self.exercise_loss_and_recovery(already_discovered=True)

    async def test_newly_discovered_valve_loss_and_recovery(self) -> None:
        await self.exercise_loss_and_recovery(already_discovered=False)

    async def test_live_session_availability_survives_scan_loss_until_cleanup(self) -> None:
        """Real manager wiring must retain availability through client handoff."""

        hass = SimpleNamespace(
            loop=asyncio.get_running_loop(),
            state=production.connection.CoreState.running,
            async_create_task=Mock(side_effect=lambda coroutine: coroutine.close()),
        )
        entry = SimpleNamespace(entry_id="test-entry", options={}, data={})
        discovery = production.discovery.ValveDiscoveryManager(hass, entry.entry_id)
        manager = production.connection.ValveConnectionManager(hass, entry, discovery)
        self.addAsyncCleanup(discovery.async_unload)
        self.addAsyncCleanup(manager.async_unload)
        await manager.async_setup()
        events = []
        discovery.async_add_listener(lambda advertisement, change: events.append(change))
        tracker = self.enterContext(
            patch.object(production.discovery, "async_track_unavailable", return_value=Mock())
        )
        self.enterContext(
            patch.object(
                production.discovery, "_classify_manufacturer_data",
                side_effect=lambda *_: production.discovery._ManufacturerClassification(
                    is_csi_device=True, model="Evb019", valve_type_full=1,
                ),
            )
        )
        info = SimpleNamespace(
            address="softener", name="CS_Meter_Soft", rssi=-50,
            manufacturer_data={}, service_data={},
        )

        def advertise() -> None:
            discovery._async_handle_bluetooth_event(
                info, production.discovery.BluetoothChange.ADVERTISEMENT
            )

        advertise()
        connection = manager.get_connection(info.address)
        self.assertIsNotNone(connection)
        await connection.async_set_persistent_connection_enabled(True)
        connection._async_fetch_device_information = AsyncMock()
        connection._async_send_reset_buffer_packet = AsyncMock(return_value=False)
        client = SimpleNamespace(is_connected=True)
        availability_during_disconnect = []

        async def disconnect() -> None:
            availability_during_disconnect.append(
                (info.address in discovery.devices, connection.available)
            )
            client.is_connected = False

        client.disconnect = AsyncMock(side_effect=disconnect)
        self.enterContext(
            patch.object(
                production.connection, "establish_connection", AsyncMock(return_value=client)
            )
        )
        await connection.async_poll()
        self.assertIs(connection._pending_persistent_client, client)
        self.assertTrue(connection.available)
        client.disconnect.assert_not_awaited()

        # Use the callback actually registered with HA, while the keepalive task
        # is still queued and has not taken ownership of the connected client.
        tracker.call_args.args[1](info)
        change = production.discovery.ValveDiscoveryChange
        self.assertEqual(events, [change.AVAILABLE])
        self.assertIn(info.address, discovery.devices)
        self.assertTrue(connection.available)

        await connection.async_set_persistent_connection_enabled(False)

        client.disconnect.assert_awaited_once()
        self.assertEqual(availability_during_disconnect, [(True, True)])
        self.assertFalse(client.is_connected)
        self.assertIsNone(connection._pending_persistent_client)
        self.assertIsNone(connection._persistent_task)
        self.assertNotIn(info.address, discovery.devices)
        self.assertFalse(connection.available)
        self.assertEqual(events, [change.AVAILABLE, change.UNAVAILABLE])

        scheduled = hass.async_create_task.call_count
        advertise()

        self.assertIs(manager.get_connection(info.address), connection)
        self.assertIn(info.address, discovery.devices)
        self.assertTrue(connection.available)
        self.assertEqual(hass.async_create_task.call_count, scheduled + 1)
        self.assertEqual(events, [change.AVAILABLE, change.UNAVAILABLE, change.AVAILABLE])
        tracker.assert_called_once()


if __name__ == "__main__":
    unittest.main()
