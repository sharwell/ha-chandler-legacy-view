"""Entry cancellation must drain BLE cleanup and release discovery subscriptions."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_availability_platforms import production
from test_entry_unload import integration


class EntryUnloadCancellationTests(unittest.IsolatedAsyncioTestCase):
    """Exercise the real entry, connection, and discovery teardown together."""

    async def test_repeated_cancellation_releases_ble_and_discovery(self) -> None:
        hass = SimpleNamespace(
            loop=asyncio.get_running_loop(),
            state=production.connection.CoreState.running,
            # Start the first poll explicitly after setting up its fake BLE I/O.
            async_create_task=Mock(side_effect=lambda coroutine: coroutine.close()),
            config_entries=SimpleNamespace(
                async_unload_platforms=AsyncMock(return_value=True)
            ),
        )
        entry = SimpleNamespace(entry_id="test-entry", data={}, options={})
        discovery = production.discovery.ValveDiscoveryManager(hass, entry.entry_id)
        manager = production.connection.ValveConnectionManager(hass, entry, discovery)
        hass.data = {
            integration.DOMAIN: {
                entry.entry_id: {
                    integration.DATA_CONNECTION_MANAGER: manager,
                    integration.DATA_DISCOVERY_MANAGER: discovery,
                }
            }
        }
        self.addAsyncCleanup(discovery.async_unload)
        self.addAsyncCleanup(manager.async_unload)

        cleanup_order = []
        advertisement_removers = []
        unavailable_removers = []

        def register_advertisement(*_):
            remove = Mock(side_effect=lambda: cleanup_order.append("advertisement"))
            advertisement_removers.append(remove)
            return remove

        def track_unavailable(*_, **__):
            remove = Mock(side_effect=lambda: cleanup_order.append("unavailable"))
            unavailable_removers.append(remove)
            return remove

        self.enterContext(patch.object(
            production.discovery, "async_register_callback",
            side_effect=register_advertisement,
        ))
        self.enterContext(patch.object(
            production.discovery, "async_track_unavailable",
            side_effect=track_unavailable,
        ))
        self.enterContext(patch.object(
            production.discovery, "_classify_manufacturer_data",
            return_value=production.discovery._ManufacturerClassification(
                is_csi_device=True, model="Evb019", valve_data_parsed=True,
            ),
        ))
        cancel_interval = Mock()
        self.enterContext(patch.object(
            production.connection, "async_track_time_interval",
            return_value=cancel_interval,
        ))

        await discovery.async_setup()
        discovery._async_handle_bluetooth_event(
            SimpleNamespace(
                address="test-valve", name="CS_Meter_Soft", rssi=-50,
                manufacturer_data={}, service_data={},
            ),
            production.discovery.BluetoothChange.ADVERTISEMENT,
        )
        await manager.async_setup()
        connection = manager.get_connection("test-valve")
        self.assertIsNotNone(connection)
        connection._persistent_connection_enabled = True
        connection._async_fetch_device_information = AsyncMock()
        connection._async_send_reset_buffer_packet = AsyncMock(return_value=False)

        disconnect_started = asyncio.Event()
        release_disconnect = asyncio.Event()
        client = SimpleNamespace(is_connected=True)

        async def disconnect() -> None:
            disconnect_started.set()
            await release_disconnect.wait()
            client.is_connected = False
            cleanup_order.append("disconnect")

        client.disconnect = AsyncMock(side_effect=disconnect)
        self.enterContext(patch.object(
            production.connection, "establish_connection",
            AsyncMock(return_value=client),
        ))
        await connection.async_poll()
        await asyncio.sleep(0)
        self.assertIsNotNone(connection._persistent_task)
        self.assertIsNone(connection._pending_persistent_client)
        self.assertEqual(discovery._connected_addresses, {"test-valve"})
        self.assertEqual(
            len(advertisement_removers), len(production.discovery.VALVE_MATCHERS)
        )
        self.assertEqual(len(unavailable_removers), 1)

        unload = asyncio.create_task(integration.async_unload_entry(hass, entry))
        try:
            async with asyncio.timeout(1):
                await disconnect_started.wait()
            for _ in range(3):
                unload.cancel()
                await asyncio.sleep(0)
                self.assertFalse(unload.done())
                self.assertTrue(client.is_connected)
                self.assertFalse(discovery._unloaded)
                self.assertIs(manager.get_connection("test-valve"), connection)
                self.assertIn(integration.DOMAIN, hass.data)
                for remove in advertisement_removers + unavailable_removers:
                    remove.assert_not_called()
        finally:
            release_disconnect.set()
            # Drain even if an assertion fails, so no test-owned session escapes.
            async with asyncio.timeout(1):
                result = await asyncio.gather(unload, return_exceptions=True)

        self.assertIsInstance(result[0], asyncio.CancelledError)
        client.disconnect.assert_awaited_once()
        self.assertFalse(client.is_connected)
        self.assertIsNone(connection._persistent_task)
        self.assertIsNone(connection._persistent_stop_task)
        self.assertIsNone(connection._pending_persistent_client)
        self.assertEqual(list(manager.get_connections()), [])
        self.assertIsNone(manager._remove_listener)
        self.assertIsNone(manager._cancel_interval)
        cancel_interval.assert_called_once_with()
        for remove in advertisement_removers + unavailable_removers:
            remove.assert_called_once_with()
        self.assertEqual(cleanup_order[0], "disconnect")
        self.assertEqual(
            len(cleanup_order),
            1 + len(advertisement_removers) + len(unavailable_removers),
        )
        self.assertTrue(discovery._unloaded)
        self.assertEqual(discovery._listeners, [])
        self.assertEqual(discovery._callbacks, [])
        self.assertEqual(discovery._unavailable_callbacks, {})
        self.assertEqual(discovery.devices, {})
        self.assertEqual(discovery._connected_addresses, set())
        self.assertNotIn(integration.DOMAIN, hass.data)


if __name__ == "__main__":
    unittest.main()
