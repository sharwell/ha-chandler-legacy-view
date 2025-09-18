"""Data models for the Chandler Legacy View integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


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
    valve_status: int | None = None
    salt_sensor_status: int | None = None
    water_status: int | None = None
    bypass_status: int | None = None
    valve_error: int | None = None
    valve_time_hours: int | None = None
    valve_time_minutes: int | None = None
    valve_type: int | None = None
    valve_series_version: int | None = None

