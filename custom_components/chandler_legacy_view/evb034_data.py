"""Normalize the existing HA readings using CsBlePacket.updatePacket rules."""

from collections.abc import Mapping
from dataclasses import fields
import math
import struct

from .cycle import CYCLE_READING, cycle_status, evb034_moving, supports_aeration, supports_soak
from .models import ValveCapabilities, ValveCycleStatus, ValveDashboardData

CYCLE_FIELDS = {"gra": 1, "grs": 12, "drcp": 9, "drtr": 65535, "drtt": 2,
                "dps": 1, "drst": 65535, "dria": 1}
READING_KEYS = {"dh", "dm", "da", "dbl", "gpf", "dtgr", "dwu", "dpfd", "dwh", "dcdo", "asdo"} | CYCLE_FIELDS.keys()
READING_FIELDS = {
    "present_flow": "present_flow", "water_usage_today": "water_usage",
    "peak_flow_today": "peak_flow", "soft_water_remaining": "water_remaining_until_regeneration",
    "days_until_regeneration": "air_recharge", "water_hardness": "water_hardness",
    "battery": "battery_capacity", "time_of_day": "time_hour",
    "display_off": "display_off", CYCLE_READING: "cycle",
}


def number(packet: Mapping, key: str) -> float | None:
    """Return an explicit finite int/float in 0..2**31-1, or None.

    Reject booleans, strings, nulls, and missing keys; do not coerce them.
    The returned number keeps its original numeric type.
    """
    value = packet.get(key)
    if type(value) not in (int, float):
        return None
    if not 0 <= value <= 0x7FFFFFFF or not math.isfinite(value):
        return None
    return value


def integer(packet: Mapping, key: str) -> int | None:
    """Truncate an accepted number toward zero, or return None as number does."""
    value = number(packet, key)
    return int(value) if value is not None else None


def valid_reading_keys(packet: Mapping, firmware: int, *, clack: bool) -> set[str]:
    """Return usable reading keys from this delta, without borrowing old values.

    ``firmware`` uses major * 100 + minor. ``clack`` selects the Clack clock
    interpretation. Check clock components individually; a key in this set
    does not establish a complete clock. ``asdo`` must be integer 0 or 1.
    The input mapping is not changed.
    """
    keys = {key for key in READING_KEYS.intersection(packet) if number(packet, key) is not None}
    for key, maximum in (("dh", 23), ("dm", 59), ("da", 1)):
        if key in keys and integer(packet, key) > maximum:
            keys.remove(key)
    if clack or firmware >= 609:
        keys.discard("da")  # The 24-hour clock does not consume this component.
    if type(packet.get("asdo")) is not int or packet["asdo"] not in (0, 1):
        keys.discard("asdo")
    keys.difference_update(
        key for key in CYCLE_FIELDS if cycle_value(packet, key) is None
    )
    return keys


def cycle_value(packet: Mapping, key: str) -> int | None:
    """Return an explicit in-range cycle integer, or None for invalid/missing data."""
    value = packet.get(key)
    if type(value) is int and 0 <= value <= CYCLE_FIELDS[key]:
        return value
    return None


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


def decode_dashboard(
    packet: Mapping, firmware: int, *, clack: bool = False,
    capabilities: ValveCapabilities | None = None,
) -> ValveDashboardData:
    """Decode a packet snapshot, leaving absent or invalid readings as None.

    ``firmware`` uses major * 100 + minor. Clack or firmware >= 611 uses
    hundredths for usage/remaining gallons; Clack or firmware >= 609 uses
    a 24-hour clock. Remaining gallons follow the reference's JVM float/int
    conversion. This does not merge deltas, enforce support/freshness, or
    mutate packet; callers supply any retained session values explicitly.
    Supply confirmed capabilities to interpret cycle position names.
    """
    values = {field.name: None for field in fields(ValveDashboardData)}
    if type(packet.get("asdo")) is int and packet["asdo"] in (0, 1):
        values["display_off"] = bool(packet["asdo"])
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
    if capabilities is not None:
        values["cycle"] = _decode_cycle(packet, capabilities)
    return ValveDashboardData(**values)


def _decode_cycle(
    packet: Mapping, capabilities: ValveCapabilities,
) -> ValveCycleStatus | None:
    def flag(key):
        value = cycle_value(packet, key)
        return bool(value) if value is not None else None

    active = flag("gra")
    motion = cycle_value(packet, "grs")
    moving = evb034_moving(motion)
    position = cycle_value(packet, "drcp")
    soaking = flag("dps") if supports_soak(capabilities) else None
    aerating = flag("dria") if supports_aeration(capabilities, evb034=True) else None
    if all(value is None for value in (active, moving, position, soaking, aerating)):
        return None

    remaining = None
    if soaking:
        estimate = cycle_value(packet, "drst")
        if estimate:
            remaining = estimate * 60
    else:
        estimate = cycle_value(packet, "drtr")
        unit = cycle_value(packet, "drtt")
        if unit is None and position is not None:
            flags = packet.get("aspo")
            if isinstance(flags, list) and position < len(flags) and type(flags[position]) is int:
                # CsValveCyclePositionOptions uses the raw position as the
                # array index. Salt-dose positions report seconds at runtime.
                mask = 0x80 if capabilities.is_clack_valve else 0x100
                unit = 0 if flags[position] & (0x08 | mask) else 1
        if estimate is not None and unit is not None:
            remaining = estimate * (60 if unit == 1 else 1)

    destination = {6: "service", 9: "brine_soak"}.get(motion)
    return cycle_status(
        capabilities, active=active, position=position, moving=moving,
        remaining=remaining, soaking=soaking, aerating=aerating, destination=destination,
    )
