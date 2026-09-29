"""Device registry regressions, runnable without Home Assistant installed.

The integration's helpers and constants are real; the framework registry is
stubbed to require config-entry-scoped identifier lookups.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from test_availability_platforms import production


def _load_registry_helpers():
    """Import the real helpers without loading the integration lifecycle."""

    modules: dict[str, ModuleType] = {}

    def module(name: str, **attributes) -> ModuleType:
        if name not in modules:
            modules[name] = ModuleType(name)
            if "." in name:
                parent, _, child = name.rpartition(".")
                setattr(module(parent), child, modules[name])
        vars(modules[name]).update(attributes)
        return modules[name]

    module("homeassistant.core", HomeAssistant=object)
    module(
        "homeassistant.const",
        Platform=SimpleNamespace(
            BINARY_SENSOR="binary_sensor", NUMBER="number", SENSOR="sensor",
            SWITCH="switch",
        ),
    )
    module("homeassistant.helpers.device_registry", async_get=Mock())

    package = "_chandler_registry_tests"
    component_path = (
        Path(__file__).resolve().parents[1] / "custom_components/chandler_legacy_view"
    )
    module(package).__path__ = [str(component_path)]
    spec = importlib.util.spec_from_file_location(
        f"{package}.device_registry", component_path / "device_registry.py"
    )
    helpers = importlib.util.module_from_spec(spec)
    modules[spec.name] = helpers
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(helpers)
    return helpers


helpers = _load_registry_helpers()


class DeviceRegistryTests(unittest.TestCase):
    """Metadata updates must not cross config entry boundaries."""

    ADDRESS = "AA:BB:CC:DD:EE:FF"
    UPDATES = (
        ("async_update_device_serial_number", "serial_number", "SERIAL-NEW"),
        ("async_update_device_sw_version", "sw_version", "2.0"),
    )

    def setUp(self) -> None:
        self.hass = object()
        self.entries = {}
        self.registry = SimpleNamespace(
            async_get_device_by_identifier=Mock(
                side_effect=lambda identifier, config_entry_id: self.entries.get(
                    (identifier, config_entry_id)
                )
            ),
            async_get_device=Mock(
                side_effect=AssertionError("Deprecated unscoped lookup used")
            ),
            async_update_device=Mock(side_effect=self._update_device),
        )
        registry_patch = patch.object(
            helpers.dr, "async_get", return_value=self.registry
        )
        self.get_registry = registry_patch.start()
        self.addCleanup(registry_patch.stop)

    def _add_device(self, config_entry_id: str, value: str | None):
        device = SimpleNamespace(
            id=f"device-{config_entry_id}", serial_number=value, sw_version=value
        )
        self.entries[((helpers.DOMAIN, self.ADDRESS), config_entry_id)] = device
        return device

    def _update_device(self, device_id: str, **changes) -> None:
        for device in self.entries.values():
            if device.id == device_id:
                for field, value in changes.items():
                    setattr(device, field, value)
                return
        self.fail(f"Updated an unknown device: {device_id}")

    def _reset_calls(self) -> None:
        self.get_registry.reset_mock()
        self.registry.async_get_device_by_identifier.reset_mock()
        self.registry.async_update_device.reset_mock()

    def test_updates_only_device_owned_by_requested_entry(self) -> None:
        for helper_name, field, value in self.UPDATES:
            for owner in ("entry-a", "entry-b"):
                with self.subTest(helper=helper_name, owner=owner):
                    self._reset_calls()
                    devices = {
                        entry: self._add_device(entry, "old")
                        for entry in ("entry-a", "entry-b")
                    }

                    getattr(helpers, helper_name)(
                        self.hass, owner, self.ADDRESS, value
                    )

                    self.get_registry.assert_called_once_with(self.hass)
                    self.registry.async_get_device_by_identifier.assert_called_once_with(
                        (helpers.DOMAIN, self.ADDRESS), owner
                    )
                    self.registry.async_update_device.assert_called_once_with(
                        devices[owner].id, **{field: value}
                    )
                    for entry, device in devices.items():
                        self.assertEqual(
                            getattr(device, field), value if entry == owner else "old"
                        )
        self.registry.async_get_device.assert_not_called()

    def test_unchanged_metadata_skips_update(self) -> None:
        for helper_name, _, value in self.UPDATES:
            with self.subTest(helper=helper_name):
                self._reset_calls()
                self._add_device("entry-a", value)

                getattr(helpers, helper_name)(
                    self.hass, "entry-a", self.ADDRESS, value
                )

                self.registry.async_update_device.assert_not_called()

    def test_missing_device_does_not_update_another_entry(self) -> None:
        for helper_name, field, value in self.UPDATES:
            with self.subTest(helper=helper_name):
                self._reset_calls()
                other_device = self._add_device("entry-b", "old")

                getattr(helpers, helper_name)(
                    self.hass, "entry-a", self.ADDRESS, value
                )

                self.registry.async_get_device_by_identifier.assert_called_once_with(
                    (helpers.DOMAIN, self.ADDRESS), "entry-a"
                )
                self.registry.async_update_device.assert_not_called()
                self.assertEqual(getattr(other_device, field), "old")

    def test_none_clears_existing_metadata(self) -> None:
        for helper_name, field, _ in self.UPDATES:
            with self.subTest(helper=helper_name):
                self._reset_calls()
                device = self._add_device("entry-a", "old")

                getattr(helpers, helper_name)(
                    self.hass, "entry-a", self.ADDRESS, None
                )

                self.registry.async_update_device.assert_called_once_with(
                    device.id, **{field: None}
                )
                self.assertIsNone(getattr(device, field))

    def test_none_preserves_already_empty_metadata(self) -> None:
        for helper_name, _, _ in self.UPDATES:
            with self.subTest(helper=helper_name):
                self._reset_calls()
                self._add_device("entry-a", None)

                getattr(helpers, helper_name)(
                    self.hass, "entry-a", self.ADDRESS, None
                )

                self.registry.async_update_device.assert_not_called()

    def test_device_list_packet_updates_and_clears_only_owning_entry(self) -> None:
        owner = self._add_device("entry-a", "old")
        other = self._add_device("entry-b", "other")
        entry = SimpleNamespace(entry_id="entry-a", options={}, data={})
        discovery = SimpleNamespace(async_set_connection_state=Mock())
        manager = production.connection.ValveConnectionManager(
            self.hass, entry, discovery
        )
        connection = manager._ensure_connection(
            production.connection.ValveAdvertisement(
                address=self.ADDRESS, name="CS_Meter_Soft", rssi=-50,
                manufacturer_data={}, service_data={},
            )
        )
        packet = bytearray(20)
        packet[0:2] = bytes((116, 116))

        with patch.object(
            production.connection,
            "async_update_device_serial_number",
            helpers.async_update_device_serial_number,
        ):
            for serial_bytes, expected in (
                (bytes.fromhex("12345678"), "12345678"),
                (bytes.fromhex("FFFFFFFF"), None),
            ):
                with self.subTest(serial=expected):
                    self._reset_calls()
                    packet[13:17] = serial_bytes

                    connection._handle_device_list_packet(bytes(packet))

                    self.registry.async_get_device_by_identifier.assert_called_once_with(
                        (helpers.DOMAIN, self.ADDRESS), entry.entry_id
                    )
                    self.registry.async_update_device.assert_called_once_with(
                        owner.id, serial_number=expected
                    )
                    self.assertEqual(connection.serial_number, expected)
                    self.assertEqual(owner.serial_number, expected)
                    self.assertEqual(other.serial_number, "other")


if __name__ == "__main__":
    unittest.main()
