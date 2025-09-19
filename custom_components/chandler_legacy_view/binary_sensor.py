"""Binary sensor platform for Chandler Legacy water system valves."""

from __future__ import annotations

from collections.abc import Callable, Mapping

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
    _can_report_low_salt,
    _is_clack_valve,
    _salt_sensor_status_display,
    _valve_error_display,
    _valve_series_display,
    _water_status_display,
    format_firmware_version,
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
        can_report_low_salt = _can_report_low_salt(self._advertisement.name)
        if self._advertisement.rssi is not None:
            attributes["rssi"] = self._advertisement.rssi
        if self._advertisement.name:
            attributes["advertised_name"] = self._advertisement.name
        formatted_version = format_firmware_version(self._advertisement)
        if formatted_version:
            attributes["firmware_version"] = formatted_version
        if self._advertisement.connection_counter is not None:
            attributes["connection_counter"] = self._advertisement.connection_counter
        if self._advertisement.bootloader_version is not None:
            attributes["bootloader_version"] = self._advertisement.bootloader_version
        if self._advertisement.radio_protocol_version is not None:
            attributes["radio_protocol_version"] = (
                self._advertisement.radio_protocol_version
            )
        if can_report_low_salt and self._advertisement.salt_sensor_status is not None:
            salt_display = _salt_sensor_status_display(
                self._advertisement.salt_sensor_status
            )
            if salt_display is not None:
                attributes["salt_sensor_status"] = salt_display
        if self._advertisement.water_status is not None:
            water_display = _water_status_display(self._advertisement.water_status)
            if water_display is not None:
                attributes["water_status"] = water_display
        if self._advertisement.bypass_status is not None:
            bypass_display = _bypass_status_display(
                self._advertisement.bypass_status
            )
            if bypass_display is not None:
                attributes["bypass_status"] = bypass_display
        if self._advertisement.valve_error is not None:
            error_display = _valve_error_display(
                self._advertisement.valve_error, is_clack_valve
            )
            if error_display is not None:
                attributes["valve_error"] = error_display
        if self._advertisement.valve_time_hours is not None:
            attributes["valve_time_hours"] = self._advertisement.valve_time_hours
        if self._advertisement.valve_time_minutes is not None:
            attributes["valve_time_minutes"] = self._advertisement.valve_time_minutes
        if self._advertisement.valve_type is not None:
            attributes["valve_type"] = self._advertisement.valve_type
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


class ValveBypassBinarySensor(ChandlerValveEntity, BinarySensorEntity):
    """Represent the bypass status reported by a water system valve."""

    def __init__(self, advertisement: ValveAdvertisement) -> None:
        super().__init__(advertisement)
        self._attr_unique_id = f"{advertisement.address}_bypass"
        self._attr_name = f"{self._attr_name} Bypass"
        self._attr_available = True
        self._update_from_advertisement(advertisement)

    def _update_from_advertisement(self, advertisement: ValveAdvertisement) -> None:
        """Update the entity state from the provided advertisement."""

        status = advertisement.bypass_status
        if status is None or status < 0:
            self._attr_is_on = None
        else:
            self._attr_is_on = status == 1

    def async_update_from_advertisement(self, advertisement: ValveAdvertisement) -> None:
        """Store advertisement details and refresh the current state."""

        super().async_update_from_advertisement(advertisement)
        self._attr_name = f"{self._attr_name} Bypass"
        self._update_from_advertisement(advertisement)

    @callback
    def async_handle_bluetooth_update(
        self, advertisement: ValveAdvertisement, change: BluetoothChange
    ) -> None:
        """Handle updates from the Bluetooth discovery manager."""

        if change in BLUETOOTH_LOST_CHANGES:
            self._attr_available = False
        else:
            self.async_update_from_advertisement(advertisement)
            self._attr_available = True
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> Mapping[str, str]:
        """Expose the textual bypass status alongside the binary state."""

        attributes: dict[str, str] = {}
        bypass_display = _bypass_status_display(self._advertisement.bypass_status)
        if bypass_display is not None:
            attributes["bypass_status"] = bypass_display
        return attributes


class ValveSaltBinarySensor(ChandlerValveEntity, BinarySensorEntity):
    """Represent the salt status reported by a water system valve."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, advertisement: ValveAdvertisement) -> None:
        super().__init__(advertisement)
        self._attr_unique_id = f"{advertisement.address}_salt"
        self._attr_name = f"{self._attr_name} Low Salt"
        self._attr_available = True
        self._update_from_advertisement(advertisement)

    def _update_from_advertisement(self, advertisement: ValveAdvertisement) -> None:
        """Update the entity state from the provided advertisement."""

        status = advertisement.salt_sensor_status
        if status is None or status < 0:
            self._attr_is_on = None
        else:
            self._attr_is_on = status == 1

    def async_update_from_advertisement(self, advertisement: ValveAdvertisement) -> None:
        """Store advertisement details and refresh the current state."""

        super().async_update_from_advertisement(advertisement)
        self._attr_name = f"{self._attr_name} Low Salt"
        self._update_from_advertisement(advertisement)

    @callback
    def async_handle_bluetooth_update(
        self, advertisement: ValveAdvertisement, change: BluetoothChange
    ) -> None:
        """Handle updates from the Bluetooth discovery manager."""

        if change in BLUETOOTH_LOST_CHANGES:
            self._attr_available = False
        else:
            self.async_update_from_advertisement(advertisement)
            self._attr_available = True
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> Mapping[str, str]:
        """Expose the textual salt status alongside the binary state."""

        attributes: dict[str, str] = {}
        salt_display = _salt_sensor_status_display(
            self._advertisement.salt_sensor_status
        )
        if salt_display is not None:
            attributes["salt_sensor_status"] = salt_display
        return attributes


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up valve presence binary sensors from a config entry."""

    entry_data = hass.data[DOMAIN][entry.entry_id]
    manager: ValveDiscoveryManager = entry_data[DATA_DISCOVERY_MANAGER]
    entities: dict[str, dict[str, ChandlerValveEntity]] = {}

    def _ensure_entities_for_advertisement(
        advertisement: ValveAdvertisement,
    ) -> tuple[dict[str, ChandlerValveEntity], list[ChandlerValveEntity]]:
        """Return existing entities and any newly created ones for an address."""

        device_entities = entities.get(advertisement.address)
        if device_entities is None:
            device_entities = {}
            entities[advertisement.address] = device_entities

        new_entities: list[ChandlerValveEntity] = []

        def _get_or_create(
            key: str, factory: Callable[[], ChandlerValveEntity]
        ) -> ChandlerValveEntity:
            entity = device_entities.get(key)
            if entity is None:
                entity = factory()
                device_entities[key] = entity
                new_entities.append(entity)
            return entity

        _get_or_create(
            "presence", lambda: ValvePresenceBinarySensor(advertisement)
        )
        _get_or_create("bypass", lambda: ValveBypassBinarySensor(advertisement))

        if _can_report_low_salt(advertisement.name):
            _get_or_create("salt", lambda: ValveSaltBinarySensor(advertisement))

        return device_entities, new_entities

    initial_entities: list[ChandlerValveEntity] = []
    for advertisement in manager.devices.values():
        _, new_entities = _ensure_entities_for_advertisement(advertisement)
        initial_entities.extend(new_entities)

    if initial_entities:
        async_add_entities(initial_entities)

    @callback
    def _handle_discovery(
        advertisement: ValveAdvertisement, change: BluetoothChange
    ) -> None:
        if change in BLUETOOTH_LOST_CHANGES:
            device_entities = entities.get(advertisement.address)
            if device_entities is None:
                return
            for entity in device_entities.values():
                entity.async_handle_bluetooth_update(advertisement, change)
            return

        device_entities, new_entities = _ensure_entities_for_advertisement(
            advertisement
        )
        if new_entities:
            async_add_entities(new_entities)
        for entity in device_entities.values():
            entity.async_handle_bluetooth_update(advertisement, change)

    remove_listener = manager.async_add_listener(_handle_discovery)
    entry.async_on_unload(remove_listener)
