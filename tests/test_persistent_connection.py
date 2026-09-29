"""Connection ownership regressions, runnable without Home Assistant installed.

Run with ``python -m unittest discover -s tests -v``. Framework services and
discovery are stubbed; connection, constants, and models are loaded unchanged.
"""

from __future__ import annotations

import asyncio
import importlib
from datetime import datetime, timezone
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch


def _load_connection_module():
    """Import the real connection code without initializing the HA integration."""

    modules: dict[str, ModuleType] = {}

    def module(name: str, **attributes) -> ModuleType:
        if name not in modules:
            modules[name] = ModuleType(name)
            if "." in name:
                parent, _, child = name.rpartition(".")
                setattr(module(parent), child, modules[name])
        vars(modules[name]).update(attributes)
        return modules[name]

    module("bleak.backends.client", BaseBleakClient=object)
    module(
        "bleak_retry_connector",
        BLEAK_RETRY_EXCEPTIONS=(OSError,),
        BleakClientWithServiceCache=object,
        establish_connection=AsyncMock(),
    )
    module(
        "homeassistant.components.bluetooth",
        BluetoothChange=object,
        async_ble_device_from_address=Mock(return_value=object()),
    )
    module("homeassistant.config_entries", ConfigEntry=object)
    module(
        "homeassistant.const",
        EVENT_HOMEASSISTANT_STARTED="homeassistant_started",
        Platform=SimpleNamespace(
            BINARY_SENSOR="binary_sensor", NUMBER="number", SENSOR="sensor",
            SWITCH="switch",
        ),
    )
    module(
        "homeassistant.core",
        CALLBACK_TYPE=object,
        CoreState=SimpleNamespace(running="running"),
        HomeAssistant=object,
        callback=lambda function: function,
    )
    module(
        "homeassistant.helpers.event",
        async_call_later=Mock(),
        async_track_time_interval=Mock(),
    )
    module("homeassistant.util.dt", utcnow=lambda: datetime.now(timezone.utc))

    # A private package name avoids importing the integration's __init__.py.
    package = "_chandler_connection_tests"
    component_path = (
        Path(__file__).resolve().parents[1] / "custom_components/chandler_legacy_view"
    )
    module(package, __path__=[str(component_path)])
    module(f"{package}.device_registry", async_update_device_serial_number=Mock())
    module(
        f"{package}.discovery",
        BLUETOOTH_LOST_CHANGES=set(),
        ValveDiscoveryManager=object,
    )
    with patch.dict(sys.modules, modules):
        return importlib.import_module(f"{package}.connection")


connection_module = _load_connection_module()


class PersistentConnectionTests(unittest.IsolatedAsyncioTestCase):
    """Exercise ownership through real polls, switch-off, and unload."""

    async def asyncSetUp(self) -> None:
        self.hass = SimpleNamespace(
            loop=asyncio.get_running_loop(),
            state=connection_module.CoreState.running,
        )
        self.connection = connection_module.ValveConnection(self.hass, "test-valve")
        self.connection.update_from_advertisement(
            connection_module.ValveAdvertisement(
                address="test-valve", name="Test valve", rssi=-50,
                manufacturer_data={}, service_data={}, model="Evb019",
            )
        )
        self.connection._persistent_connection_enabled = True
        self.connection._async_fetch_device_information = AsyncMock()
        self.connection._async_send_reset_buffer_packet = AsyncMock(return_value=False)
        self.connection.schedule_poll = Mock()
        self.disconnected = asyncio.Event()
        self.client = SimpleNamespace(is_connected=True)

        async def disconnect() -> None:
            self.client.is_connected = False
            self.disconnected.set()

        self.client.disconnect = AsyncMock(side_effect=disconnect)
        connector = patch.object(
            connection_module, "establish_connection",
            AsyncMock(return_value=self.client),
        )
        connector.start()
        self.addCleanup(connector.stop)
        self.addAsyncCleanup(self.connection.async_unload)

    def assert_disconnected_once(self) -> None:
        self.client.disconnect.assert_awaited_once()
        self.assertFalse(self.client.is_connected)
        self.assertIsNone(self.connection._persistent_task)

    async def test_unload_before_persistent_task_starts(self) -> None:
        # The mocked I/O completes synchronously, leaving keepalive queued.
        await self.connection.async_poll()
        await self.connection.async_unload()

        self.assert_disconnected_once()

    async def test_disable_before_persistent_task_starts(self) -> None:
        await self.connection.async_poll()
        await self.connection.async_set_persistent_connection_enabled(False)

        self.assert_disconnected_once()

    async def test_concurrent_stops_before_persistent_task_starts(self) -> None:
        # Both stop requests are already queued when the poll hands off its client.
        disable = asyncio.create_task(
            self.connection.async_set_persistent_connection_enabled(False)
        )
        unload = asyncio.create_task(self.connection.async_unload())
        await self.connection.async_poll()
        await asyncio.gather(disable, unload)

        self.assert_disconnected_once()

    async def test_unload_waits_for_pending_client_disconnect(self) -> None:
        disconnect_started = asyncio.Event()
        release_disconnect = asyncio.Event()

        async def disconnect() -> None:
            disconnect_started.set()
            await release_disconnect.wait()
            self.client.is_connected = False

        self.client.disconnect.side_effect = disconnect
        unload = asyncio.create_task(self.connection.async_unload())
        await self.connection.async_poll()
        try:
            async with asyncio.timeout(1):
                await disconnect_started.wait()
            self.assertFalse(unload.done())
            self.assertTrue(self.client.is_connected)
        finally:
            release_disconnect.set()
            await unload

        self.assert_disconnected_once()

    async def test_disable_after_persistent_task_starts(self) -> None:
        polling = asyncio.Event()

        async def dashboard(client):
            polling.set()
            await asyncio.Future()

        self.connection._async_request_dashboard = AsyncMock(side_effect=dashboard)
        self.connection._persistent_poll_interval = 0
        with patch.object(connection_module, "MIN_PERSISTENT_POLL_INTERVAL_SECONDS", 0):
            await self.connection.async_poll()
            async with asyncio.timeout(1):
                await polling.wait()
            await self.connection.async_set_persistent_connection_enabled(False)

        self.assert_disconnected_once()

    async def test_normal_persistent_exit_disconnects_once(self) -> None:
        self.connection._async_request_dashboard = AsyncMock(return_value=(False, False))
        self.connection._persistent_poll_interval = 0
        with patch.object(connection_module, "MIN_PERSISTENT_POLL_INTERVAL_SECONDS", 0):
            await self.connection.async_poll()
            async with asyncio.timeout(1):
                await self.connection._persistent_task
            await self.connection.async_unload()

        self.assert_disconnected_once()

    async def test_task_creation_failure_leaves_cleanup_with_poll(self) -> None:
        self.hass.loop = SimpleNamespace(
            create_task=Mock(side_effect=RuntimeError("Task creation failed"))
        )
        with self.assertRaisesRegex(RuntimeError, "Task creation failed"):
            await self.connection.async_poll()
        await self.connection.async_unload()

        self.assert_disconnected_once()


if __name__ == "__main__":
    unittest.main()
