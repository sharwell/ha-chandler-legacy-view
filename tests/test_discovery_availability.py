"""Discovery availability regressions without Home Assistant installed."""

from __future__ import annotations

from enum import Enum
import importlib
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch


def _load_discovery_module():
    """Import real discovery with HA's advertisement-only event contract."""

    modules: dict[str, ModuleType] = {}

    def module(name: str, **attributes) -> ModuleType:
        if name not in modules:
            modules[name] = ModuleType(name)
            if "." in name:
                parent, _, child = name.rpartition(".")
                setattr(module(parent), child, modules[name])
        vars(modules[name]).update(attributes)
        return modules[name]

    module(
        "homeassistant.components.bluetooth",
        BluetoothChange=Enum("BluetoothChange", "ADVERTISEMENT"),
        BluetoothScanningMode=SimpleNamespace(PASSIVE="passive"),
        BluetoothServiceInfoBleak=SimpleNamespace,
        async_register_callback=Mock(),
        async_track_unavailable=Mock(),
    )
    module(
        "homeassistant.const",
        Platform=SimpleNamespace(
            BINARY_SENSOR="binary_sensor", NUMBER="number", SENSOR="sensor",
            SWITCH="switch",
        ),
    )
    module(
        "homeassistant.core", CALLBACK_TYPE=object, HomeAssistant=object,
        callback=lambda function: function,
    )

    package = "_chandler_discovery_tests"
    component_path = (
        Path(__file__).resolve().parents[1] / "custom_components/chandler_legacy_view"
    )
    module(package, __path__=[str(component_path)])
    module(f"{package}.device_registry", async_update_device_sw_version=Mock())
    module(
        f"{package}.entity",
        _is_clack_valve=lambda name: name.casefold().startswith("cl_"),
        format_firmware_version=lambda advertisement: str(
            advertisement.firmware_version
        ),
    )
    with patch.dict(sys.modules, modules):
        return importlib.import_module(f"{package}.discovery")


discovery_module = _load_discovery_module()


class DiscoveryAvailabilityTests(unittest.IsolatedAsyncioTestCase):
    """Drive HA callbacks through the real valve availability state machine."""

    async def asyncSetUp(self) -> None:
        self.hass = object()
        self.manager = discovery_module.ValveDiscoveryManager(self.hass)
        self.events: list[tuple[object, object]] = []
        self.remove_listener = self.manager.async_add_listener(
            lambda advertisement, change: self.events.append((advertisement, change))
        )
        self.advertisement_removers: list[Mock] = []
        self.unavailable_removers: list[Mock] = []
        self.unavailable_callbacks: dict[str, object] = {}

        def register_advertisement(hass, callback, matcher, scanning_mode):
            remove = Mock()
            self.advertisement_removers.append(remove)
            return remove

        def track_unavailable(hass, callback, address, connectable=True):
            self.unavailable_callbacks[address] = callback
            remove = Mock()
            self.unavailable_removers.append(remove)
            return remove

        self.register_advertisement = self.enterContext(
            patch.object(
                discovery_module, "async_register_callback",
                side_effect=register_advertisement,
            )
        )
        self.track_unavailable = self.enterContext(
            patch.object(
                discovery_module, "async_track_unavailable",
                side_effect=track_unavailable,
            )
        )
        self.classify = self.enterContext(
            patch.object(
                discovery_module, "_classify_manufacturer_data",
                side_effect=lambda *_: discovery_module._ManufacturerClassification(
                    is_csi_device=True, firmware_version=412, model="Evb019",
                    valve_type_full=1, valve_data_parsed=True,
                ),
            )
        )
        await self.manager.async_setup()
        self.addAsyncCleanup(self.manager.async_unload)

    def service_info(self, address="valve-a", **overrides):
        values = {
            "address": address,
            "name": "CS_Meter_Soft",
            "rssi": -50,
            "manufacturer_data": {1850: b"valve"},
            "service_data": {},
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def advertise(self, address="valve-a", **overrides):
        info = self.service_info(address, **overrides)
        self.manager._async_handle_bluetooth_event(
            info, discovery_module.BluetoothChange.ADVERTISEMENT
        )
        return info

    def lose(self, address="valve-a") -> None:
        self.unavailable_callbacks[address](self.service_info(address))

    @property
    def changes(self):
        return [change for _, change in self.events]

    async def test_registers_passive_discovery_with_advertisement_only_api(self):
        self.assertEqual(
            list(discovery_module.BluetoothChange),
            [discovery_module.BluetoothChange.ADVERTISEMENT],
        )
        self.assertEqual(
            self.register_advertisement.call_args_list,
            [
                call(
                    self.hass, self.manager._async_handle_bluetooth_event,
                    matcher, discovery_module.BluetoothScanningMode.PASSIVE,
                )
                for matcher in discovery_module.VALVE_MATCHERS
            ],
        )
        self.track_unavailable.assert_not_called()

    async def test_rejected_advertisements_do_not_create_unavailable_watchers(self):
        self.advertise(name="Unrelated device")
        self.classify.assert_not_called()
        self.classify.side_effect = None
        self.classify.return_value = discovery_module._ManufacturerClassification(
            is_csi_device=False
        )
        self.advertise()
        self.classify.return_value = discovery_module._ManufacturerClassification(
            is_csi_device=True, ignore_advertisement=True
        )
        self.advertise()

        self.track_unavailable.assert_not_called()
        self.assertEqual(self.manager.devices, {})
        self.assertEqual(self.events, [])

    async def test_one_connectable_watcher_per_accepted_address(self):
        self.advertise()
        self.advertise(rssi=-55)
        self.advertise("valve-b")

        self.assertEqual(self.track_unavailable.call_count, 2)
        self.assertEqual(
            self.track_unavailable.call_args_list,
            [
                call(
                    self.hass, self.manager._async_handle_unavailable,
                    address, connectable=True,
                )
                for address in ("valve-a", "valve-b")
            ],
        )
        self.lose()
        self.assertEqual(set(self.manager.devices), {"valve-b"})
        self.assertEqual(self.events[-1][0].address, "valve-a")
        self.assertIs(
            self.events[-1][1], discovery_module.ValveDiscoveryChange.UNAVAILABLE
        )

    async def test_unavailable_uses_latest_accepted_metadata_once(self):
        self.advertise(rssi=-40)
        self.advertise(rssi=-67)
        latest = self.manager.devices["valve-a"]
        self.lose()
        self.lose()
        self.manager._async_handle_unavailable(self.service_info("unknown"))

        self.assertIs(self.events[-1][0], latest)
        self.assertEqual(latest.rssi, -67)
        self.assertEqual(len(self.events), 3)
        self.assertEqual(self.manager.devices, {})

    async def test_rejected_advertisement_does_not_restore_unavailable_valve(self):
        self.advertise()
        self.lose()
        self.classify.side_effect = None
        self.classify.return_value = discovery_module._ManufacturerClassification(
            is_csi_device=True, ignore_advertisement=True
        )
        self.advertise()

        self.assertEqual(self.manager.devices, {})
        self.assertEqual(len(self.events), 2)
        self.track_unavailable.assert_called_once()

    async def test_failing_listener_does_not_block_unavailable_for_other_listeners(self):
        self.remove_listener()
        failed_notifications = Mock()

        def failing_listener(advertisement, change):
            if change is discovery_module.ValveDiscoveryChange.UNAVAILABLE:
                failed_notifications(advertisement)
                raise RuntimeError("entity was removed during unload")

        self.manager.async_add_listener(failing_listener)
        self.manager.async_add_listener(
            lambda advertisement, change: self.events.append((advertisement, change))
        )
        self.advertise()
        with self.assertLogs(discovery_module.__name__, level="ERROR") as logs:
            self.lose()
        self.assertIn("entity was removed during unload", "\n".join(logs.output))
        with self.assertNoLogs(discovery_module.__name__, level="ERROR"):
            self.lose()

        failed_notifications.assert_called_once()
        self.assertEqual(self.manager.devices, {})
        self.assertEqual(
            self.changes,
            [discovery_module.ValveDiscoveryChange.AVAILABLE,
             discovery_module.ValveDiscoveryChange.UNAVAILABLE],
        )

    async def test_watcher_survives_recovery_and_second_loss(self):
        self.advertise()
        self.lose()
        self.advertise(rssi=-45)
        self.assertEqual(set(self.manager.devices), {"valve-a"})
        self.lose()

        change = discovery_module.ValveDiscoveryChange
        self.assertEqual(
            self.changes,
            [change.AVAILABLE, change.UNAVAILABLE, change.AVAILABLE, change.UNAVAILABLE],
        )
        self.track_unavailable.assert_called_once()
        self.unavailable_removers[0].assert_not_called()
        self.assertEqual(self.manager.devices, {})

    async def test_incomplete_recovery_retains_previous_valve_metadata(self):
        self.advertise()
        self.lose()
        self.classify.side_effect = None
        self.classify.return_value = discovery_module._ManufacturerClassification(
            is_csi_device=True, manufacturer_data_complete=False,
        )
        self.advertise(rssi=-61, manufacturer_data={1850: b"partial"})

        advertisement = self.manager.devices["valve-a"]
        self.assertEqual(advertisement.model, "Evb019")
        self.assertEqual(advertisement.firmware_version, 412)
        self.assertEqual(advertisement.valve_type, "MeteredSoftener")
        self.assertEqual(advertisement.rssi, -61)
        self.assertEqual(advertisement.manufacturer_data, {1850: b"partial"})
        self.assertFalse(advertisement.manufacturer_data_complete)
        self.assertFalse(advertisement.valve_data_parsed)
        self.assertIs(
            self.events[-1][1], discovery_module.ValveDiscoveryChange.AVAILABLE
        )

    async def test_connected_session_stays_available_without_advertisements(self):
        self.advertise()
        self.manager.async_set_connection_state("valve-a", True)
        self.lose()
        self.lose()

        self.assertEqual(set(self.manager.devices), {"valve-a"})
        self.assertEqual(self.changes, [discovery_module.ValveDiscoveryChange.AVAILABLE])

        self.manager.async_set_connection_state("valve-a", False)
        self.manager.async_set_connection_state("valve-a", False)
        self.assertEqual(self.manager.devices, {})
        self.assertEqual(
            self.changes,
            [discovery_module.ValveDiscoveryChange.AVAILABLE,
             discovery_module.ValveDiscoveryChange.UNAVAILABLE],
        )

    async def test_connect_success_after_scan_loss_restores_availability(self):
        self.advertise()
        self.lose()
        self.manager.async_set_connection_state("valve-a", True)
        self.manager.async_set_connection_state("valve-a", True)
        self.assertEqual(set(self.manager.devices), {"valve-a"})
        self.manager.async_set_connection_state("valve-a", False)

        change = discovery_module.ValveDiscoveryChange
        self.assertEqual(
            self.changes,
            [change.AVAILABLE, change.UNAVAILABLE, change.AVAILABLE, change.UNAVAILABLE],
        )

    async def test_fresh_advertisement_prevents_unavailable_on_session_end(self):
        self.advertise()
        self.manager.async_set_connection_state("valve-a", True)
        self.lose()
        self.advertise(rssi=-42)
        self.manager.async_set_connection_state("valve-a", False)

        self.assertEqual(self.manager.devices["valve-a"].rssi, -42)
        self.assertEqual(
            self.changes,
            [discovery_module.ValveDiscoveryChange.AVAILABLE] * 2,
        )

    async def test_connection_state_does_not_invent_unknown_devices(self):
        self.manager.async_set_connection_state("unknown", True)
        self.manager.async_set_connection_state("unknown", False)

        self.assertEqual(self.manager.devices, {})
        self.assertEqual(self.events, [])
        self.track_unavailable.assert_not_called()

    async def test_unload_removes_watchers_including_currently_unavailable_valves(self):
        self.advertise()
        self.advertise("valve-b")
        self.lose()
        self.manager.async_set_connection_state("valve-b", True)
        await self.manager.async_unload()
        await self.manager.async_unload()

        for remove in self.advertisement_removers + self.unavailable_removers:
            remove.assert_called_once()
        self.assertEqual(len(self.unavailable_removers), 2)
        self.assertEqual(self.manager.devices, {})

    async def test_late_callbacks_after_unload_do_not_recreate_devices_or_watchers(self):
        self.advertise()
        await self.manager.async_unload()
        event_count = len(self.events)

        self.advertise(rssi=-48)
        self.lose()
        self.manager.async_set_connection_state("valve-a", True)
        self.manager.async_set_connection_state("valve-a", False)

        self.assertEqual(self.manager.devices, {})
        self.assertEqual(len(self.events), event_count)
        self.track_unavailable.assert_called_once()


if __name__ == "__main__":
    unittest.main()
