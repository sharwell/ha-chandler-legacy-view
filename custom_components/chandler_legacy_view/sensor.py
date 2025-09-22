"""Sensor platform for Chandler Legacy View valves."""

from __future__ import annotations

import logging

from homeassistant.components.bluetooth import BluetoothChange
from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfVolumeFlowRate
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DATA_CONNECTION_MANAGER, DATA_DISCOVERY_MANAGER, DOMAIN
from .connection import ValveConnection, ValveConnectionManager
from .discovery import BLUETOOTH_LOST_CHANGES, ValveDiscoveryManager
from .entity import ChandlerValveEntity
from .models import ValveAdvertisement, ValveDashboardData

_LOGGER = logging.getLogger(__name__)


class ValvePresentFlowSensor(ChandlerValveEntity, SensorEntity):
    """Represent the present flow rate reported by a valve dashboard packet."""

    _attr_native_unit_of_measurement = UnitOfVolumeFlowRate.GALLONS_PER_MINUTE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, advertisement: ValveAdvertisement, connection: ValveConnection
    ) -> None:
        super().__init__(advertisement)
        self._attr_unique_id = f"{advertisement.address}_present_flow"
        self._attr_name = f"{self._attr_name} Present Flow"
        self._attr_available = True
        self._remove_dashboard_listener: CALLBACK_TYPE | None = None
        self._update_from_dashboard(connection.dashboard_data, write_state=False)
        self._remove_dashboard_listener = connection.add_dashboard_listener(
            self._handle_dashboard_update
        )

    @callback
    def async_handle_bluetooth_update(
        self, advertisement: ValveAdvertisement, change: BluetoothChange
    ) -> None:
        """Handle Bluetooth discovery updates for this valve."""

        if change in BLUETOOTH_LOST_CHANGES:
            self._attr_available = False
        else:
            self.async_update_from_advertisement(advertisement)
            self._attr_available = True

        if self.hass is not None:
            self.async_write_ha_state()

    def async_update_from_advertisement(
        self, advertisement: ValveAdvertisement
    ) -> None:
        """Store the most recent advertisement for the valve."""

        super().async_update_from_advertisement(advertisement)
        self._attr_name = f"{self._attr_name} Present Flow"

    async def async_will_remove_from_hass(self) -> None:
        """Clean up listeners when the entity is removed."""

        await super().async_will_remove_from_hass()
        if self._remove_dashboard_listener is not None:
            self._remove_dashboard_listener()
            self._remove_dashboard_listener = None

    def _update_from_dashboard(
        self, dashboard: ValveDashboardData | None, *, write_state: bool
    ) -> None:
        """Update the native value from dashboard data."""

        self._attr_native_value = (
            None if dashboard is None else dashboard.present_flow
        )
        if write_state and self.hass is not None:
            self.async_write_ha_state()

    @callback
    def _handle_dashboard_update(
        self, dashboard: ValveDashboardData | None
    ) -> None:
        """Handle updates from the dashboard poller."""

        self._update_from_dashboard(dashboard, write_state=True)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up present flow sensors for Chandler valves."""

    entry_data = hass.data[DOMAIN][entry.entry_id]
    discovery_manager: ValveDiscoveryManager = entry_data[DATA_DISCOVERY_MANAGER]
    connection_manager: ValveConnectionManager = entry_data[DATA_CONNECTION_MANAGER]

    entities: dict[str, ValvePresentFlowSensor] = {}

    def _ensure_entity(
        advertisement: ValveAdvertisement,
    ) -> tuple[ValvePresentFlowSensor | None, list[ValvePresentFlowSensor]]:
        """Return existing and newly created entities for an advertisement."""

        entity = entities.get(advertisement.address)
        new_entities: list[ValvePresentFlowSensor] = []

        if entity is None:
            connection = connection_manager.get_connection(advertisement.address)
            if connection is None:
                _LOGGER.debug(
                    "Delaying present flow sensor creation for %s; connection not ready",
                    advertisement.address,
                )
                return None, new_entities

            entity = ValvePresentFlowSensor(advertisement, connection)
            entities[advertisement.address] = entity
            new_entities.append(entity)

        return entity, new_entities

    initial_entities: list[ValvePresentFlowSensor] = []
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
