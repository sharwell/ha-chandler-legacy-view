"""Sensor platform for Chandler Legacy View valves."""

from __future__ import annotations

import logging
from collections.abc import Callable

from homeassistant.components.bluetooth import BluetoothChange
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfTime,
    UnitOfVolume,
    UnitOfVolumeFlowRate,
)

# Home Assistant does not currently expose a dedicated water hardness unit
# constant, so we keep using the unit string Chandler devices report.
WATER_HARDNESS_GRAINS_PER_GALLON = "grains_per_gallon"
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DATA_CONNECTION_MANAGER, DATA_DISCOVERY_MANAGER, DOMAIN
from .connection import ValveConnection, ValveConnectionManager
from .discovery import BLUETOOTH_LOST_CHANGES, ValveDiscoveryManager
from .entity import ChandlerValveEntity, _is_clack_valve
from .models import ValveAdvertisement, ValveDashboardData

_LOGGER = logging.getLogger(__name__)


class ValveDashboardSensor(ChandlerValveEntity, SensorEntity):
    """Base class for sensors driven by dashboard packet updates."""

    _attr_available = True

    def __init__(
        self,
        advertisement: ValveAdvertisement,
        connection: ValveConnection,
        *,
        unique_id_suffix: str,
        name_suffix: str | None,
    ) -> None:
        super().__init__(advertisement)
        base_name = self._attr_name
        self._attr_unique_id = f"{advertisement.address}_{unique_id_suffix}"
        self._name_suffix = name_suffix
        self._remove_dashboard_listener: CALLBACK_TYPE | None = None
        self._attr_name = self._apply_name_suffix(base_name, advertisement)
        self._update_from_dashboard(connection.dashboard_data, write_state=False)
        self._remove_dashboard_listener = connection.add_dashboard_listener(
            self._handle_dashboard_update
        )

    def _apply_name_suffix(
        self, base_name: str, advertisement: ValveAdvertisement
    ) -> str:
        suffix = self._get_name_suffix(advertisement)
        if suffix:
            return f"{base_name} {suffix}"
        return base_name

    def _get_name_suffix(self, advertisement: ValveAdvertisement) -> str | None:
        """Return the display suffix for the entity name."""

        return self._name_suffix

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
        base_name = self._attr_name
        self._attr_name = self._apply_name_suffix(base_name, advertisement)

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

        self._attr_native_value = self._extract_native_value(dashboard)
        if write_state and self.hass is not None:
            self.async_write_ha_state()

    def _extract_native_value(
        self, dashboard: ValveDashboardData | None
    ) -> object | None:
        """Return the sensor's value derived from dashboard data."""

        raise NotImplementedError

    @callback
    def _handle_dashboard_update(
        self, dashboard: ValveDashboardData | None
    ) -> None:
        """Handle updates from the dashboard poller."""

        self._update_from_dashboard(dashboard, write_state=True)


class ValvePresentFlowSensor(ValveDashboardSensor):
    """Represent the present flow rate reported by a valve dashboard packet."""

    _attr_native_unit_of_measurement = UnitOfVolumeFlowRate.GALLONS_PER_MINUTE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, advertisement: ValveAdvertisement, connection: ValveConnection
    ) -> None:
        super().__init__(
            advertisement,
            connection,
            unique_id_suffix="present_flow",
            name_suffix="Present Flow",
        )

    def _extract_native_value(
        self, dashboard: ValveDashboardData | None
    ) -> float | None:
        if dashboard is None:
            return None
        return dashboard.present_flow


class ValveWaterHardnessSensor(ValveDashboardSensor):
    """Represent the configured water hardness reported by a valve."""

    _attr_native_unit_of_measurement = WATER_HARDNESS_GRAINS_PER_GALLON
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, advertisement: ValveAdvertisement, connection: ValveConnection
    ) -> None:
        super().__init__(
            advertisement,
            connection,
            unique_id_suffix="water_hardness",
            name_suffix="Water Hardness",
        )

    def _extract_native_value(
        self, dashboard: ValveDashboardData | None
    ) -> int | None:
        if dashboard is None:
            return None
        return dashboard.water_hardness


class ValveTimeOfDaySensor(ValveDashboardSensor):
    """Represent the current valve time reported by the dashboard."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(
        self, advertisement: ValveAdvertisement, connection: ValveConnection
    ) -> None:
        super().__init__(
            advertisement,
            connection,
            unique_id_suffix="time_of_day",
            name_suffix="Time of Day",
        )

    def _extract_native_value(
        self, dashboard: ValveDashboardData | None
    ) -> object | None:
        if dashboard is None:
            return None
        hour_value = dashboard.time_hour
        minute = dashboard.time_minute
        if hour_value < 0 or minute < 0 or minute >= 60:
            return None

        hour = hour_value % 24
        if dashboard.is_pm:
            if hour < 12:
                hour = (hour % 12) + 12
        elif hour == 12:
            hour = 0

        try:
            now = dt_util.now()
            return now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        except ValueError:
            return None


class ValveBatteryCapacitySensor(ValveDashboardSensor):
    """Represent the battery capacity reported by a non-Clack valve."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.BATTERY

    def __init__(
        self, advertisement: ValveAdvertisement, connection: ValveConnection
    ) -> None:
        super().__init__(
            advertisement,
            connection,
            unique_id_suffix="battery",
            name_suffix="Battery",
        )

    def _extract_native_value(
        self, dashboard: ValveDashboardData | None
    ) -> int | None:
        if dashboard is None:
            return None
        return dashboard.battery_capacity


class ValveSoftWaterRemainingSensor(ValveDashboardSensor):
    """Represent the soft water remaining until regeneration for metered valves."""

    _attr_native_unit_of_measurement = UnitOfVolume.GALLONS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, advertisement: ValveAdvertisement, connection: ValveConnection
    ) -> None:
        super().__init__(
            advertisement,
            connection,
            unique_id_suffix="soft_water_remaining",
            name_suffix="Soft Water Remaining",
        )

    def _extract_native_value(
        self, dashboard: ValveDashboardData | None
    ) -> int | None:
        if dashboard is None:
            return None
        return dashboard.water_remaining_until_regeneration


class ValveDaysUntilRegenerationSensor(ValveDashboardSensor):
    """Represent the countdown until the next scheduled regeneration."""

    _attr_native_unit_of_measurement = UnitOfTime.DAYS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, advertisement: ValveAdvertisement, connection: ValveConnection
    ) -> None:
        super().__init__(
            advertisement,
            connection,
            unique_id_suffix="days_until_regeneration",
            name_suffix=None,
        )

    def _get_name_suffix(self, advertisement: ValveAdvertisement) -> str | None:
        if advertisement.valve_type == "TimeClockSoftener":
            return "Days Until Regeneration"
        return "Days Until Backwash"

    def _extract_native_value(
        self, dashboard: ValveDashboardData | None
    ) -> int | None:
        if dashboard is None:
            return None
        return dashboard.air_recharge


class ValveWaterUsageTodaySensor(ValveDashboardSensor):
    """Represent the total water usage recorded for the current day."""

    _attr_native_unit_of_measurement = UnitOfVolume.GALLONS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, advertisement: ValveAdvertisement, connection: ValveConnection
    ) -> None:
        super().__init__(
            advertisement,
            connection,
            unique_id_suffix="water_usage_today",
            name_suffix="Water Usage Today",
        )

    def _extract_native_value(
        self, dashboard: ValveDashboardData | None
    ) -> int | None:
        if dashboard is None:
            return None
        return dashboard.water_usage


class ValvePeakFlowTodaySensor(ValveDashboardSensor):
    """Represent the peak flow recorded for the current day."""

    _attr_native_unit_of_measurement = UnitOfVolumeFlowRate.GALLONS_PER_MINUTE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, advertisement: ValveAdvertisement, connection: ValveConnection
    ) -> None:
        super().__init__(
            advertisement,
            connection,
            unique_id_suffix="peak_flow_today",
            name_suffix="Peak Flow Today",
        )

    def _extract_native_value(
        self, dashboard: ValveDashboardData | None
    ) -> float | None:
        if dashboard is None:
            return None
        return dashboard.peak_flow


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up dashboard-driven sensors for Chandler valves."""

    entry_data = hass.data[DOMAIN][entry.entry_id]
    discovery_manager: ValveDiscoveryManager = entry_data[DATA_DISCOVERY_MANAGER]
    connection_manager: ValveConnectionManager = entry_data[DATA_CONNECTION_MANAGER]

    flow_entities: dict[str, ValvePresentFlowSensor] = {}
    hardness_entities: dict[str, ValveWaterHardnessSensor] = {}
    time_entities: dict[str, ValveTimeOfDaySensor] = {}
    battery_entities: dict[str, ValveBatteryCapacitySensor] = {}
    soft_water_entities: dict[str, ValveSoftWaterRemainingSensor] = {}
    days_entities: dict[str, ValveDaysUntilRegenerationSensor] = {}
    usage_entities: dict[str, ValveWaterUsageTodaySensor] = {}
    peak_entities: dict[str, ValvePeakFlowTodaySensor] = {}

    def _ensure_dashboard_entity(
        advertisement: ValveAdvertisement,
        entity_map: dict[str, ValveDashboardSensor],
        *,
        predicate: Callable[[ValveAdvertisement], bool] | None = None,
        factory: Callable[[ValveAdvertisement, ValveConnection], ValveDashboardSensor],
        debug_description: str,
    ) -> tuple[ValveDashboardSensor | None, list[ValveDashboardSensor]]:
        entity = entity_map.get(advertisement.address)
        new_entities: list[ValveDashboardSensor] = []

        if entity is None:
            if predicate is not None and not predicate(advertisement):
                return None, new_entities

            connection = connection_manager.get_connection(advertisement.address)
            if connection is None:
                _LOGGER.debug(
                    "Delaying %s sensor creation for %s; connection not ready",
                    debug_description,
                    advertisement.address,
                )
                return None, new_entities

            entity = factory(advertisement, connection)
            entity_map[advertisement.address] = entity
            new_entities.append(entity)

        return entity, new_entities

    def _ensure_flow_entity(
        advertisement: ValveAdvertisement,
    ) -> tuple[ValveDashboardSensor | None, list[ValveDashboardSensor]]:
        return _ensure_dashboard_entity(
            advertisement,
            flow_entities,
            factory=lambda adv, conn: ValvePresentFlowSensor(adv, conn),
            debug_description="present flow",
        )

    def _ensure_hardness_entity(
        advertisement: ValveAdvertisement,
    ) -> tuple[ValveDashboardSensor | None, list[ValveDashboardSensor]]:
        return _ensure_dashboard_entity(
            advertisement,
            hardness_entities,
            predicate=lambda adv: adv.is_metered_softener,
            factory=lambda adv, conn: ValveWaterHardnessSensor(adv, conn),
            debug_description="water hardness",
        )

    def _ensure_time_entity(
        advertisement: ValveAdvertisement,
    ) -> tuple[ValveDashboardSensor | None, list[ValveDashboardSensor]]:
        return _ensure_dashboard_entity(
            advertisement,
            time_entities,
            factory=lambda adv, conn: ValveTimeOfDaySensor(adv, conn),
            debug_description="time of day",
        )

    def _ensure_battery_entity(
        advertisement: ValveAdvertisement,
    ) -> tuple[ValveDashboardSensor | None, list[ValveDashboardSensor]]:
        return _ensure_dashboard_entity(
            advertisement,
            battery_entities,
            predicate=lambda adv: not _is_clack_valve(adv.name),
            factory=lambda adv, conn: ValveBatteryCapacitySensor(adv, conn),
            debug_description="battery",
        )

    def _ensure_soft_water_entity(
        advertisement: ValveAdvertisement,
    ) -> tuple[ValveDashboardSensor | None, list[ValveDashboardSensor]]:
        return _ensure_dashboard_entity(
            advertisement,
            soft_water_entities,
            predicate=lambda adv: adv.is_metered_softener,
            factory=lambda adv, conn: ValveSoftWaterRemainingSensor(adv, conn),
            debug_description="soft water remaining",
        )

    def _ensure_days_entity(
        advertisement: ValveAdvertisement,
    ) -> tuple[ValveDashboardSensor | None, list[ValveDashboardSensor]]:
        return _ensure_dashboard_entity(
            advertisement,
            days_entities,
            predicate=lambda adv: not adv.is_metered_softener,
            factory=lambda adv, conn: ValveDaysUntilRegenerationSensor(adv, conn),
            debug_description="days until regeneration",
        )

    def _ensure_usage_entity(
        advertisement: ValveAdvertisement,
    ) -> tuple[ValveDashboardSensor | None, list[ValveDashboardSensor]]:
        return _ensure_dashboard_entity(
            advertisement,
            usage_entities,
            factory=lambda adv, conn: ValveWaterUsageTodaySensor(adv, conn),
            debug_description="water usage today",
        )

    def _ensure_peak_entity(
        advertisement: ValveAdvertisement,
    ) -> tuple[ValveDashboardSensor | None, list[ValveDashboardSensor]]:
        return _ensure_dashboard_entity(
            advertisement,
            peak_entities,
            factory=lambda adv, conn: ValvePeakFlowTodaySensor(adv, conn),
            debug_description="peak flow today",
        )

    EnsureCallback = Callable[
        [ValveAdvertisement],
        tuple[ValveDashboardSensor | None, list[ValveDashboardSensor]],
    ]

    ensure_callbacks: tuple[EnsureCallback, ...] = (
        _ensure_flow_entity,
        _ensure_hardness_entity,
        _ensure_time_entity,
        _ensure_battery_entity,
        _ensure_soft_water_entity,
        _ensure_days_entity,
        _ensure_usage_entity,
        _ensure_peak_entity,
    )

    initial_entities: list[SensorEntity] = []
    for advertisement in discovery_manager.devices.values():
        for ensure_callback in ensure_callbacks:
            _, created = ensure_callback(advertisement)
            initial_entities.extend(created)

    if initial_entities:
        async_add_entities(initial_entities)

    entity_maps = (
        flow_entities,
        hardness_entities,
        time_entities,
        battery_entities,
        soft_water_entities,
        days_entities,
        usage_entities,
        peak_entities,
    )

    @callback
    def _handle_discovery(
        advertisement: ValveAdvertisement, change: BluetoothChange
    ) -> None:
        if change in BLUETOOTH_LOST_CHANGES:
            for entity_map in entity_maps:
                entity = entity_map.get(advertisement.address)
                if entity is not None:
                    entity.async_handle_bluetooth_update(advertisement, change)
            return

        results = [ensure_callback(advertisement) for ensure_callback in ensure_callbacks]

        new_entities: list[SensorEntity] = []
        for _, created in results:
            new_entities.extend(created)

        if new_entities:
            async_add_entities(new_entities)

        for entity, _ in results:
            if entity is not None:
                entity.async_handle_bluetooth_update(advertisement, change)

    remove_listener = discovery_manager.async_add_listener(_handle_discovery)
    entry.async_on_unload(remove_listener)
