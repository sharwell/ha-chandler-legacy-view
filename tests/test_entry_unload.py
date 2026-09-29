"""Config entry unload regressions, runnable without Home Assistant installed.

Run with ``python -m unittest discover -s tests -v``. Framework services and
manager classes are stubbed; the integration entry module and constants are real.
"""

from __future__ import annotations

import asyncio
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
    """Preserve runtime until platforms unload, then finish all cleanup."""

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

    async def test_cancelled_connection_cleanup_preserves_other_entries(self) -> None:
        other_data = object()
        self.domain_data["other-entry"] = other_data
        self.connection_manager.async_unload.side_effect = asyncio.CancelledError

        with self.assertRaises(asyncio.CancelledError):
            await integration.async_unload_entry(self.hass, self.entry)

        self.connection_manager.async_unload.assert_awaited_once_with()
        self.discovery_manager.async_unload.assert_awaited_once_with()
        self.assertIs(self.hass.data[integration.DOMAIN], self.domain_data)
        self.assertNotIn(self.entry.entry_id, self.domain_data)
        self.assertIs(self.domain_data["other-entry"], other_data)

    async def test_connection_cleanup_error_still_unloads_discovery(self) -> None:
        failure = RuntimeError("Connection cleanup failed")
        self.connection_manager.async_unload.side_effect = failure

        with self.assertRaises(RuntimeError) as raised:
            await integration.async_unload_entry(self.hass, self.entry)

        self.assertIs(raised.exception, failure)
        self.discovery_manager.async_unload.assert_awaited_once_with()
        self.assertNotIn(integration.DOMAIN, self.hass.data)

    async def test_cancelled_platform_unload_preserves_runtime(self) -> None:
        self.hass.config_entries.async_unload_platforms.side_effect = (
            asyncio.CancelledError
        )

        with self.assertRaises(asyncio.CancelledError):
            await integration.async_unload_entry(self.hass, self.entry)

        self.assertIs(self.hass.data[integration.DOMAIN], self.domain_data)
        self.assertIs(self.domain_data[self.entry.entry_id], self.entry_data)
        self.connection_manager.async_unload.assert_not_called()
        self.discovery_manager.async_unload.assert_not_called()

    async def test_concurrent_unload_does_not_mask_cancellation(self) -> None:
        connection_cleanup_started = asyncio.Event()

        async def wait_for_cancellation() -> None:
            connection_cleanup_started.set()
            await asyncio.Future()

        self.connection_manager.async_unload.side_effect = wait_for_cancellation
        other_entry = SimpleNamespace(entry_id="other-entry")
        other_discovery = SimpleNamespace(async_unload=AsyncMock())
        self.domain_data[other_entry.entry_id] = {
            integration.DATA_CONNECTION_MANAGER: SimpleNamespace(async_unload=AsyncMock()),
            integration.DATA_DISCOVERY_MANAGER: other_discovery,
        }
        first_unload = asyncio.create_task(
            integration.async_unload_entry(self.hass, self.entry)
        )
        try:
            async with asyncio.timeout(1):
                await connection_cleanup_started.wait()
            self.assertTrue(await integration.async_unload_entry(self.hass, other_entry))
            self.assertNotIn(integration.DOMAIN, self.hass.data)
        finally:
            first_unload.cancel()
            results = await asyncio.gather(first_unload, return_exceptions=True)

        self.assertIsInstance(results[0], asyncio.CancelledError)
        self.discovery_manager.async_unload.assert_awaited_once_with()
        other_discovery.async_unload.assert_awaited_once_with()
        self.assertNotIn(integration.DOMAIN, self.hass.data)


if __name__ == "__main__":
    unittest.main()
