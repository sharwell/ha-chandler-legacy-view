"""Normalize the existing HA readings using CsBlePacket.updatePacket rules."""

from collections.abc import Mapping
from dataclasses import fields
import math
import struct

from .models import ValveDashboardData

READING_KEYS = {"dh", "dm", "da", "dbl", "gpf", "dtgr", "dwu", "dpfd", "dwh", "dcdo"}


def number(packet: Mapping, key: str) -> float | None:
    value = packet.get(key)
    if type(value) not in (int, float):
        return None
    if not 0 <= value <= 0x7FFFFFFF or not math.isfinite(value):
        return None
    return value


def integer(packet: Mapping, key: str) -> int | None:
    value = number(packet, key)
    return int(value) if value is not None else None


def _battery_capacity(raw: float) -> int:
    # Number.floatValue()/1000 is a JVM float before the double-based curve.
    voltage = struct.unpack("f", struct.pack("f", raw / 1000))[0]
    if voltage >= 9.5:
        return 100
    for threshold, baseline, rate, upper in (
        (8.91, 100, 8.78, 9.5), (8.48, 94.78, 30.26, 8.91),
        (7.43, 81.84, 60.47, 8.48), (6.5, 18.68, 20.02, 7.43),
    ):
        if voltage >= threshold:
            return max(0, int(baseline - (upper - voltage) * rate))
    return 0


def decode_dashboard(packet: Mapping, firmware: int, *, clack: bool = False) -> ValveDashboardData:
    """Retain absent readings as unknown; apply each field's own conversion."""
    values = {field.name: None for field in fields(ValveDashboardData)}
    divisor = 100 if clack or firmware >= 611 else 1
    for key, field, scale, truncate in (
        ("gpf", "present_flow", 100, False),
        ("dpfd", "peak_flow", 100, False),
        ("dwu", "water_usage", divisor, False),
        ("dtgr", "water_remaining_until_regeneration", divisor, True),
        ("dwh", "water_hardness", 1, True),
        ("dcdo", "air_recharge", 1, True),
    ):
        value = number(packet, key)
        if value is not None:
            normalized = value / scale
            if key == "dtgr":
                # updatePacket casts Number to JVM float and divides as float
                # before float-to-int. Large counters can otherwise differ by
                # a gallon when decoded with Python's double precision.
                raw_float = struct.unpack("f", struct.pack("f", value))[0]
                normalized = struct.unpack("f", struct.pack("f", raw_float / scale))[0]
                # JVM float-to-int saturates when rounding exceeds Int.MAX_VALUE.
                normalized = min(normalized, 0x7FFFFFFF)
            values[field] = int(normalized) if truncate else normalized
    hour, minute = integer(packet, "dh"), integer(packet, "dm")
    am_pm = integer(packet, "da")
    # getFullTimeString changes to a 24-hour field at C6.09, before the C6.11
    # volume conversion boundary. Clack always uses the 24-hour interpretation.
    if hour is not None and hour <= 23 and minute is not None and minute < 60:
        if clack or firmware >= 609:
            values.update(time_hour=hour, time_minute=minute, is_pm=hour >= 12)
        elif am_pm in (0, 1):
            values.update(time_hour=hour, time_minute=minute, is_pm=bool(am_pm))
    raw_battery = number(packet, "dbl")
    if raw_battery is not None:
        values["battery_capacity"] = _battery_capacity(raw_battery)
    return ValveDashboardData(**values)
