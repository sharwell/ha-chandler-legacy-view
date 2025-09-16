"""Binary sensor platform for Chandler Legacy water system valves."""

from __future__ import annotations

from collections.abc import Mapping

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.components.bluetooth import BluetoothChange
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_DISCOVERY_MANAGER, DOMAIN
from .discovery import ValveDiscoveryManager
from .entity import ChandlerValveEntity
from .models import ValveAdvertisement


class ValvePresenceBinarySensor(ChandlerValveEntity, BinarySensorEntity):
    """Represent the presence of a water system valve detected via Bluetooth."""

    _attr_device_class = BinarySensorDeviceClass.PRESENCE

    def __init__(self, advertisement: ValveAdvertisement) -> None:
        super().__init__(advertisement)
        self._attr_is_on = True
        self._attr_available = True

    @callback
    def async_handle_bluetooth_update(
        self, advertisement: ValveAdvertisement, change: BluetoothChange
    ) -> None:
        """Handle updates from the Bluetooth discovery manager."""

        if change is BluetoothChange.LOST:
            self._attr_is_on = False
            self._attr_available = False
        else:
            self._attr_is_on = True
            self.async_update_from_advertisement(advertisement)
            self._attr_available = True
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> Mapping[str, int | str]:
        """Provide metadata about the most recent advertisement."""

        attributes: dict[str, int | str] = {}
        if self._advertisement.rssi is not None:
            attributes["rssi"] = self._advertisement.rssi
        if self._advertisement.name:
            attributes["advertised_name"] = self._advertisement.name
        if self._advertisement.firmware_version is not None:
            attributes["firmware_version"] = self._advertisement.firmware_version
        if self._advertisement.firmware_major is not None:
            attributes["firmware_major"] = self._advertisement.firmware_major
        if self._advertisement.firmware_minor is not None:
            attributes["firmware_minor"] = self._advertisement.firmware_minor
        return attributes


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up valve presence binary sensors from a config entry."""

    entry_data = hass.data[DOMAIN][entry.entry_id]
    manager: ValveDiscoveryManager = entry_data[DATA_DISCOVERY_MANAGER]
    entities: dict[str, ValvePresenceBinarySensor] = {}

    initial_entities = [
        ValvePresenceBinarySensor(advertisement)
        for advertisement in manager.devices.values()
    ]
    for entity in initial_entities:
        entities[entity.unique_id] = entity

    if initial_entities:
        async_add_entities(initial_entities)

    @callback
    def _handle_discovery(
        advertisement: ValveAdvertisement, change: BluetoothChange
    ) -> None:
        entity = entities.get(advertisement.address)
        if entity is not None:
            entity.async_handle_bluetooth_update(advertisement, change)
            return

        if change is BluetoothChange.LOST:
            return

        new_entity = ValvePresenceBinarySensor(advertisement)
        entities[advertisement.address] = new_entity
        async_add_entities([new_entity])

    remove_listener = manager.async_add_listener(_handle_discovery)
    entry.async_on_unload(remove_listener)
