"""Switch platform for Chandler Legacy water system valves."""

from __future__ import annotations

import logging

from homeassistant.components.bluetooth import BluetoothChange
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_CONNECTION_MANAGER, DATA_DISCOVERY_MANAGER, DOMAIN
from .connection import ValveConnection, ValveConnectionManager
from .discovery import BLUETOOTH_LOST_CHANGES, ValveDiscoveryManager
from .entity import ChandlerValveEntity
from .models import ValveAdvertisement

_LOGGER = logging.getLogger(__name__)


class ValveAuthenticationLockoutSwitch(ChandlerValveEntity, SwitchEntity):
    """Represent the authentication lockout state for a valve."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, advertisement: ValveAdvertisement, connection: ValveConnection
    ) -> None:
        super().__init__(advertisement)
        self._connection = connection
        self._attr_unique_id = f"{advertisement.address}_authentication_lockout"
        self._attr_name = f"{self._attr_name} Authentication Lockout"
        self._attr_available = advertisement.authentication_required
        self._attr_is_on = connection.authentication_lockout
        self._remove_authentication_listener: CALLBACK_TYPE | None = (
            connection.add_authentication_listener(self._handle_authentication_update)
        )

    @callback
    def async_handle_bluetooth_update(
        self, advertisement: ValveAdvertisement, change: BluetoothChange
    ) -> None:
        """Handle Bluetooth discovery updates for the valve."""

        if change in BLUETOOTH_LOST_CHANGES:
            self._attr_available = False
        else:
            self.async_update_from_advertisement(advertisement)
            self._attr_available = advertisement.authentication_required

        if self.hass is not None:
            self.async_write_ha_state()

    def async_update_from_advertisement(
        self, advertisement: ValveAdvertisement
    ) -> None:
        """Store the latest advertisement details for the valve."""

        super().async_update_from_advertisement(advertisement)
        self._attr_name = f"{self._attr_name} Authentication Lockout"

    async def async_will_remove_from_hass(self) -> None:
        """Clean up callbacks before the entity is removed."""

        await super().async_will_remove_from_hass()
        if self._remove_authentication_listener is not None:
            self._remove_authentication_listener()
            self._remove_authentication_listener = None

    @callback
    def _handle_authentication_update(self, locked: bool) -> None:
        """Handle updates from the valve connection."""

        self._attr_is_on = locked
        if self.hass is not None:
            self.async_write_ha_state()

    async def async_turn_on(self, **kwargs) -> None:
        """Manually enable the authentication lockout."""

        self._connection.set_authentication_lockout(True)
        self._attr_is_on = True
        if self.hass is not None:
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Allow the next connection attempt to retry authentication."""

        self._connection.clear_authentication_lockout()
        self._attr_is_on = self._connection.authentication_lockout
        if self.hass is not None:
            self.async_write_ha_state()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up authentication lockout switches for Chandler valves."""

    entry_data = hass.data[DOMAIN][entry.entry_id]
    discovery_manager: ValveDiscoveryManager = entry_data[DATA_DISCOVERY_MANAGER]
    connection_manager: ValveConnectionManager = entry_data[DATA_CONNECTION_MANAGER]

    entities: dict[str, ValveAuthenticationLockoutSwitch] = {}

    def _ensure_entity(
        advertisement: ValveAdvertisement,
    ) -> tuple[ValveAuthenticationLockoutSwitch | None, list[ValveAuthenticationLockoutSwitch]]:
        """Return existing and newly created entities for an advertisement."""

        entity = entities.get(advertisement.address)
        new_entities: list[ValveAuthenticationLockoutSwitch] = []

        if entity is None:
            if not advertisement.authentication_required:
                return None, new_entities

            connection = connection_manager.get_connection(advertisement.address)
            if connection is None:
                _LOGGER.debug(
                    "Delaying authentication lockout entity creation for %s; connection not ready",
                    advertisement.address,
                )
                return None, new_entities

            entity = ValveAuthenticationLockoutSwitch(advertisement, connection)
            entities[advertisement.address] = entity
            new_entities.append(entity)

        return entity, new_entities

    initial_entities: list[ValveAuthenticationLockoutSwitch] = []
    for advertisement in discovery_manager.devices.values():
        _, new_entities = _ensure_entity(advertisement)
        initial_entities.extend(new_entities)

    if initial_entities:
        async_add_entities(initial_entities)

    @callback
    def _handle_discovery(
        advertisement: ValveAdvertisement, change: BluetoothChange
    ) -> None:
        if change in BLUETOOTH_LOST_CHANGES:
            entity = entities.get(advertisement.address)
            if entity is not None:
                entity.async_handle_bluetooth_update(advertisement, change)
            return

        entity, new_entities = _ensure_entity(advertisement)
        if entity is None:
            return

        if new_entities:
            async_add_entities(new_entities)

        entity.async_handle_bluetooth_update(advertisement, change)

    remove_listener = discovery_manager.async_add_listener(_handle_discovery)
    entry.async_on_unload(remove_listener)
