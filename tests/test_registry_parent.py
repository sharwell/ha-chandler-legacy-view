"""Ensure setup retains the correct registry parent for each config entry."""

from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, call, patch

from test_entry_unload import integration


class RegistryParentTests(unittest.IsolatedAsyncioTestCase):
    """Use registry IDs without sharing a parent across config entries."""

    def setUp(self) -> None:
        self.hass = SimpleNamespace(
            data={},
            config_entries=SimpleNamespace(
                async_update_entry=Mock(),
                async_forward_entry_setups=AsyncMock(),
            ),
            bus=SimpleNamespace(async_listen_once=Mock(return_value=Mock())),
        )
        self.registry = SimpleNamespace(async_get_or_create=Mock())
        self.enterContext(
            patch.object(integration.dr, "async_get", return_value=self.registry, create=True)
        )
        self.enterContext(
            patch.object(integration, "DeviceEntryType", SimpleNamespace(SERVICE="service"))
        )
        self.discovery_factory = self.enterContext(
            patch.object(integration, "ValveDiscoveryManager")
        )
        self.connection_factory = self.enterContext(
            patch.object(integration, "ValveConnectionManager")
        )

    def make_entry(self, entry_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            entry_id=entry_id,
            data={integration.CONF_DEFAULT_PASSCODE: integration.DEFAULT_VALVE_PASSCODE},
            options={},
            async_on_unload=Mock(),
            add_update_listener=Mock(return_value=Mock()),
        )

    def make_managers(self) -> tuple[SimpleNamespace, SimpleNamespace]:
        return (
            SimpleNamespace(async_setup=AsyncMock()),
            SimpleNamespace(async_setup=AsyncMock(), async_shutdown=AsyncMock()),
        )

    async def test_parent_registry_id_is_available_before_platform_setup(self) -> None:
        entry = self.make_entry("first-entry")
        parent_id = "registry-generated-parent-id"
        self.registry.async_get_or_create.return_value = SimpleNamespace(id=parent_id)
        discovery, connection = self.make_managers()
        self.discovery_factory.return_value = discovery
        self.connection_factory.return_value = connection

        async def forward_platforms(forwarded_entry, platforms) -> None:
            self.assertIs(forwarded_entry, entry)
            self.assertEqual(platforms, integration.PLATFORMS)
            runtime = self.hass.data[integration.DOMAIN][entry.entry_id]
            self.assertEqual(runtime[integration.DATA_DISCOVERY_DEVICE_ID], parent_id)
            self.assertIs(runtime[integration.DATA_DISCOVERY_MANAGER], discovery)
            self.assertIs(runtime[integration.DATA_CONNECTION_MANAGER], connection)
            discovery.async_setup.assert_awaited_once_with()
            connection.async_setup.assert_awaited_once_with()

        self.hass.config_entries.async_forward_entry_setups.side_effect = forward_platforms

        self.assertTrue(await integration.async_setup_entry(self.hass, entry))

        self.discovery_factory.assert_called_once_with(self.hass, entry.entry_id)
        self.connection_factory.assert_called_once_with(self.hass, entry, discovery)
        self.registry.async_get_or_create.assert_called_once_with(
            config_entry_id=entry.entry_id,
            identifiers={(integration.DOMAIN, integration.DISCOVERY_VIA_DEVICE_ID)},
            manufacturer=integration.DEFAULT_MANUFACTURER,
            model=integration.DISCOVERY_DEVICE_MODEL,
            name=integration.DISCOVERY_DEVICE_NAME,
            entry_type="service",
        )
        self.hass.config_entries.async_forward_entry_setups.assert_awaited_once_with(
            entry, integration.PLATFORMS
        )

    async def test_entries_keep_distinct_parent_ids_with_the_same_identifier(self) -> None:
        entries = (self.make_entry("first-entry"), self.make_entry("second-entry"))
        parent_ids = ("first-registry-parent", "second-registry-parent")
        managers = (self.make_managers(), self.make_managers())
        self.registry.async_get_or_create.side_effect = [
            SimpleNamespace(id=parent_id) for parent_id in parent_ids
        ]
        self.discovery_factory.side_effect = [pair[0] for pair in managers]
        self.connection_factory.side_effect = [pair[1] for pair in managers]

        for entry in entries:
            self.assertTrue(await integration.async_setup_entry(self.hass, entry))

        self.assertEqual(
            self.discovery_factory.call_args_list,
            [call(self.hass, entry.entry_id) for entry in entries],
        )
        for entry, parent_id, (discovery, connection) in zip(entries, parent_ids, managers):
            runtime = self.hass.data[integration.DOMAIN][entry.entry_id]
            self.assertEqual(runtime[integration.DATA_DISCOVERY_DEVICE_ID], parent_id)
            self.assertIs(runtime[integration.DATA_DISCOVERY_MANAGER], discovery)
            self.assertIs(runtime[integration.DATA_CONNECTION_MANAGER], connection)

        parent_calls = self.registry.async_get_or_create.call_args_list
        self.assertEqual(len(parent_calls), 2)
        self.assertEqual(
            [item.kwargs["config_entry_id"] for item in parent_calls],
            [entry.entry_id for entry in entries],
        )
        for parent_call in parent_calls:
            self.assertEqual(
                parent_call.kwargs["identifiers"],
                {(integration.DOMAIN, integration.DISCOVERY_VIA_DEVICE_ID)},
            )


if __name__ == "__main__":
    unittest.main()
