"""Data models for the Chandler Legacy View integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

_METERED_SOFTENER_VALVE_TYPES = {
    "MeteredSoftener",
    "CommercialMeteredSoftener",
}


@dataclass(frozen=True, slots=True)
class ValveCapabilities:
    """Confirmed metadata for the Water System 6.18.2 Dashboard display rules.

    CsDashboardViewModel.setupValveOptions uses CsValveType.isMeteredSoftener
    for hardness, remaining water, and days. None of the currently exposed
    Dashboard rows has a firmware cutoff; the battery row excludes Clack.
    """

    is_twin_valve: bool | None = None
    valve_type: str | None = None

    @property
    def is_metered_softener(self) -> bool | None:
        """Distinguish an unresolved valve type from a confirmed non-metered valve."""

        if self.is_twin_valve:
            return True
        if self.valve_type is None:
            return None
        return self.valve_type in _METERED_SOFTENER_VALVE_TYPES


@dataclass(slots=True)
class ValveAdvertisement:
    """Representation of a Bluetooth advertisement emitted by a valve."""

    address: str
    name: str | None
    rssi: int | None
    manufacturer_data: Mapping[int, bytes]
    service_data: Mapping[str, bytes]
    firmware_major: int | None = None
    firmware_minor: int | None = None
    firmware_version: int | None = None
    model: str | None = None
    is_twin_valve: bool = False
    is_400_series: bool = False
    has_connection_counter: bool | None = None
    valve_data_parsed: bool = False
    manufacturer_data_complete: bool = True
    valve_status: int | None = None
    salt_sensor_status: int | None = None
    water_status: int | None = None
    bypass_status: int | None = None
    valve_error: int | None = None
    valve_time_hours: int | None = None
    valve_time_minutes: int | None = None
    valve_type_full: int | None = None
    valve_type: str | None = None
    valve_series_version: int | None = None
    connection_counter: int | None = None
    bootloader_version: int | None = None
    radio_protocol_version: int | None = None
    authentication_required: bool | None = None

    @property
    def is_metered_softener(self) -> bool:
        """Return ``True`` if the advertisement describes a metered softener valve."""

        if self.is_twin_valve:
            return True

        valve_type = self.valve_type
        if valve_type is None:
            return False

        return valve_type in _METERED_SOFTENER_VALVE_TYPES


@dataclass(slots=True)
class ValveDashboardData:
    """Normalized Dashboard readings; absent Evb034 fields remain unknown."""

    time_hour: int | None
    time_minute: int | None
    is_pm: bool | None
    battery_capacity: int | None
    present_flow: float | None
    water_remaining_until_regeneration: int | None
    water_usage: float | None
    peak_flow: float | None
    water_hardness: int | None
    regeneration_time_hour: int
    regeneration_time_is_pm: bool
    shutoff_setting_enabled: bool
    bypass_setting_enabled: bool
    shutoff_active: bool
    bypass_active: bool
    display_off: bool
    filter_backwash: int
    air_recharge: int | None
    pos_time: int
    pos_option_seconds: int
    regen_cycle_position: int
    regen_active: int
    prefill_soak_mode: bool
    soak_timer: int
    is_in_aeration: bool
    tank_in_service: int
    graph_usage_ten_gallons: tuple[int, ...]

