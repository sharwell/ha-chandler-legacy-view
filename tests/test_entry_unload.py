"""Config entry unload regressions, runnable without Home Assistant installed.

Run with ``python -m unittest discover -s tests -v``. Framework services and
manager classes are stubbed; the integration entry module and constants are real.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch


def _load_integration_module():
    """Load the actual entry lifecycle without requiring Home Assistant."""

    modules: dict[str, ModuleType] = {}

    def module(name: str, **attributes) -> ModuleType:
        if name not in modules:
            modules[name] = ModuleType(name)
            if "." in name:
                parent, _, child = name.rpartition(".")
                setattr(module(parent), child, modules[name])
        vars(modules[name]).update(attributes)
        return modules[name]

    module("homeassistant.config_entries", ConfigEntry=object)
    module(
        "homeassistant.const",
        EVENT_HOMEASSISTANT_STOP="homeassistant_stop",
        Platform=SimpleNamespace(
            BINARY_SENSOR="binary_sensor", NUMBER="number", SENSOR="sensor",
            SWITCH="switch",
        ),
    )
    module("homeassistant.core", HomeAssistant=object)
    module("homeassistant.helpers.device_registry", DeviceEntryType=object)
    module("homeassistant.helpers.typing", ConfigType=dict)

    package = "_chandler_entry_tests"
    component_path = (
        Path(__file__).resolve().parents[1] / "custom_components/chandler_legacy_view"
    )
    spec = importlib.util.spec_from_file_location(
        package, component_path / "__init__.py",
        submodule_search_locations=[str(component_path)],
    )
    integration = importlib.util.module_from_spec(spec)
    modules[package] = integration
    module(f"{package}.connection", ValveConnectionManager=object)
    module(f"{package}.discovery", ValveDiscoveryManager=object)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(integration)
    return integration


integration = _load_integration_module()


class EntryUnloadTests(unittest.IsolatedAsyncioTestCase):
    """Preserve shared runtime state until platform unloading succeeds."""

    def setUp(self) -> None:
        self.entry = SimpleNamespace(entry_id="test-entry")
        self.connection_manager = SimpleNamespace(async_unload=AsyncMock())
        self.discovery_manager = SimpleNamespace(async_unload=AsyncMock())
        self.entry_data = {
            integration.DATA_CONNECTION_MANAGER: self.connection_manager,
            integration.DATA_DISCOVERY_MANAGER: self.discovery_manager,
        }
        self.domain_data = {self.entry.entry_id: self.entry_data}
        self.hass = SimpleNamespace(
            data={integration.DOMAIN: self.domain_data},
            config_entries=SimpleNamespace(
                async_unload_platforms=AsyncMock(return_value=True)
            ),
        )

    async def test_failed_unload_preserves_runtime_and_allows_retry(self) -> None:
        unload_platforms = self.hass.config_entries.async_unload_platforms
        unload_platforms.side_effect = [False, False, True]

        for _ in range(2):
            self.assertFalse(await integration.async_unload_entry(self.hass, self.entry))
            self.assertIs(self.hass.data[integration.DOMAIN], self.domain_data)
            self.assertIs(self.domain_data[self.entry.entry_id], self.entry_data)
            self.assertIs(
                self.entry_data[integration.DATA_CONNECTION_MANAGER],
                self.connection_manager,
            )
            self.assertIs(
                self.entry_data[integration.DATA_DISCOVERY_MANAGER],
                self.discovery_manager,
            )
            self.connection_manager.async_unload.assert_not_called()
            self.discovery_manager.async_unload.assert_not_called()

        self.assertTrue(await integration.async_unload_entry(self.hass, self.entry))
        self.assertEqual(unload_platforms.await_count, 3)
        unload_platforms.assert_awaited_with(self.entry, integration.PLATFORMS)
        self.connection_manager.async_unload.assert_awaited_once_with()
        self.discovery_manager.async_unload.assert_awaited_once_with()
        self.assertNotIn(integration.DOMAIN, self.hass.data)

    async def test_successful_unload_preserves_other_entries(self) -> None:
        other_connection = SimpleNamespace(async_unload=AsyncMock())
        other_discovery = SimpleNamespace(async_unload=AsyncMock())
        other_data = {
            integration.DATA_CONNECTION_MANAGER: other_connection,
            integration.DATA_DISCOVERY_MANAGER: other_discovery,
        }
        self.domain_data["other-entry"] = other_data

        self.assertTrue(await integration.async_unload_entry(self.hass, self.entry))

        self.assertIs(self.hass.data[integration.DOMAIN], self.domain_data)
        self.assertNotIn(self.entry.entry_id, self.domain_data)
        self.assertIs(self.domain_data["other-entry"], other_data)
        self.connection_manager.async_unload.assert_awaited_once_with()
        self.discovery_manager.async_unload.assert_awaited_once_with()
        other_connection.async_unload.assert_not_called()
        other_discovery.async_unload.assert_not_called()


if __name__ == "__main__":
    unittest.main()
