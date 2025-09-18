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
from .discovery import BLUETOOTH_LOST_CHANGES, ValveDiscoveryManager
from .entity import (
    ChandlerValveEntity,
    _VALVE_SERIES_EVB034_DISPLAY,
    _VALVE_SERIES_EBX044_DISPLAY,
    _bypass_status_display,
    _is_clack_valve,
    _salt_sensor_status_display,
    _valve_error_display,
    _valve_series_display,
    _valve_type_display,
    _water_status_display,
)
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

        if change in BLUETOOTH_LOST_CHANGES:
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
        is_clack_valve = _is_clack_valve(self._advertisement.name)
        if self._advertisement.rssi is not None:
            attributes["rssi"] = self._advertisement.rssi
        if self._advertisement.name:
            attributes["advertised_name"] = self._advertisement.name
        if self._advertisement.firmware_version is not None:
            attributes["firmware_version"] = self._advertisement.firmware_version
            formatted_version = self._format_firmware_version(self._advertisement)
            if formatted_version:
                attributes["firmware_display"] = formatted_version
        if self._advertisement.firmware_major is not None:
            attributes["firmware_major"] = self._advertisement.firmware_major
        if self._advertisement.firmware_minor is not None:
            attributes["firmware_minor"] = self._advertisement.firmware_minor
        if self._advertisement.model:
            attributes["model"] = self._advertisement.model
        if self._advertisement.valve_status is not None:
            attributes["valve_status"] = self._advertisement.valve_status
        if self._advertisement.salt_sensor_status is not None:
            attributes["salt_sensor_status"] = (
                self._advertisement.salt_sensor_status
            )
            salt_display = _salt_sensor_status_display(
                self._advertisement.salt_sensor_status
            )
            if salt_display is not None:
                attributes["salt_sensor_status_display"] = salt_display
        if self._advertisement.water_status is not None:
            attributes["water_status"] = self._advertisement.water_status
            water_display = _water_status_display(self._advertisement.water_status)
            if water_display is not None:
                attributes["water_status_display"] = water_display
        if self._advertisement.bypass_status is not None:
            attributes["bypass_status"] = self._advertisement.bypass_status
            bypass_display = _bypass_status_display(
                self._advertisement.bypass_status
            )
            if bypass_display is not None:
                attributes["bypass_status_display"] = bypass_display
        if self._advertisement.valve_error is not None:
            attributes["valve_error"] = self._advertisement.valve_error
            error_display = _valve_error_display(
                self._advertisement.valve_error, is_clack_valve
            )
            if error_display is not None:
                attributes["valve_error_display"] = error_display
        if self._advertisement.valve_time_hours is not None:
            attributes["valve_time_hours"] = self._advertisement.valve_time_hours
        if self._advertisement.valve_time_minutes is not None:
            attributes["valve_time_minutes"] = self._advertisement.valve_time_minutes
        if self._advertisement.valve_type is not None:
            attributes["valve_type"] = self._advertisement.valve_type
            type_display = _valve_type_display(self._advertisement.valve_type)
            if type_display is not None:
                attributes["valve_type_display"] = type_display
        if self._advertisement.valve_series_version is not None:
            attributes["valve_series_version"] = (
                self._advertisement.valve_series_version
            )
            evb034_display = _valve_series_display(
                _VALVE_SERIES_EVB034_DISPLAY, self._advertisement.valve_series_version
            )
            if evb034_display is not None:
                attributes["valve_series_version_evb034_display"] = evb034_display
            ebx044_display = _valve_series_display(
                _VALVE_SERIES_EBX044_DISPLAY, self._advertisement.valve_series_version
            )
            if ebx044_display is not None:
                attributes["valve_series_version_ebx044_display"] = ebx044_display
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

        if change in BLUETOOTH_LOST_CHANGES:
            return

        new_entity = ValvePresenceBinarySensor(advertisement)
        entities[advertisement.address] = new_entity
        async_add_entities([new_entity])

    remove_listener = manager.async_add_listener(_handle_discovery)
    entry.async_on_unload(remove_listener)
