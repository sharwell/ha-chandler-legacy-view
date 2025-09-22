"""Number entities for configuring Chandler valves."""

from __future__ import annotations

import logging

from homeassistant.components.bluetooth import BluetoothChange
from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DATA_CONNECTION_MANAGER,
    DATA_DISCOVERY_MANAGER,
    DOMAIN,
    MAX_PERSISTENT_POLL_INTERVAL_SECONDS,
    MIN_PERSISTENT_POLL_INTERVAL_SECONDS,
)
from .connection import ValveConnection, ValveConnectionManager
from .discovery import BLUETOOTH_LOST_CHANGES, ValveDiscoveryManager
from .entity import ChandlerValveEntity
from .models import ValveAdvertisement

_LOGGER = logging.getLogger(__name__)


class ValvePersistentPollIntervalNumber(ChandlerValveEntity, NumberEntity):
    """Configure the polling interval used while a persistent connection is active."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_native_min_value = MIN_PERSISTENT_POLL_INTERVAL_SECONDS
    _attr_native_max_value = MAX_PERSISTENT_POLL_INTERVAL_SECONDS
    _attr_native_step = 1.0

    def __init__(
        self, advertisement: ValveAdvertisement, connection: ValveConnection
    ) -> None:
        super().__init__(advertisement)
        self._connection = connection
        self._attr_unique_id = f"{advertisement.address}_persistent_poll_interval"
        self._attr_name = f"{self._attr_name} Persistent Poll Interval"
        self._attr_available = True
        self._attr_native_value = connection.persistent_poll_interval

    @callback
    def async_handle_bluetooth_update(
        self, advertisement: ValveAdvertisement, change: BluetoothChange
    ) -> None:
        """Handle Bluetooth discovery updates for the valve."""

        if change in BLUETOOTH_LOST_CHANGES:
            self._attr_available = False
        else:
            self.async_update_from_advertisement(advertisement)
            self._attr_available = True

        self._attr_native_value = self._connection.persistent_poll_interval
        if self.hass is not None:
            self.async_write_ha_state()

    def async_update_from_advertisement(
        self, advertisement: ValveAdvertisement
    ) -> None:
        """Store the latest advertisement details for the valve."""

        super().async_update_from_advertisement(advertisement)
        self._attr_name = f"{self._attr_name} Persistent Poll Interval"

    async def async_set_native_value(self, value: float) -> None:
        """Update the configured persistent polling interval."""

        await self._connection.async_set_persistent_poll_interval(value)
        self._attr_native_value = self._connection.persistent_poll_interval
        if self.hass is not None:
            self.async_write_ha_state()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up persistent poll interval numbers for Chandler valves."""

    entry_data = hass.data[DOMAIN][entry.entry_id]
    discovery_manager: ValveDiscoveryManager = entry_data[DATA_DISCOVERY_MANAGER]
    connection_manager: ValveConnectionManager = entry_data[DATA_CONNECTION_MANAGER]

    entities: dict[str, ValvePersistentPollIntervalNumber] = {}

    def _ensure_entity(
        advertisement: ValveAdvertisement,
    ) -> tuple[ValvePersistentPollIntervalNumber | None, list[ValvePersistentPollIntervalNumber]]:
        entity = entities.get(advertisement.address)
        new_entities: list[ValvePersistentPollIntervalNumber] = []

        if entity is None:
            connection = connection_manager.get_connection(advertisement.address)
            if connection is None:
                _LOGGER.debug(
                    "Delaying persistent poll interval entity creation for %s; connection not ready",
                    advertisement.address,
                )
                return None, new_entities

            entity = ValvePersistentPollIntervalNumber(advertisement, connection)
            entities[advertisement.address] = entity
            new_entities.append(entity)

        return entity, new_entities

    initial_entities: list[ValvePersistentPollIntervalNumber] = []
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
