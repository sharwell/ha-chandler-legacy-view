"""Base entities for the Chandler Legacy View integration."""

from __future__ import annotations

from collections.abc import Mapping

from homeassistant.helpers.entity import DeviceInfo, Entity

from .const import (
    DEFAULT_FRIENDLY_NAME,
    DEFAULT_MANUFACTURER,
    DISCOVERY_VIA_DEVICE_ID,
    DOMAIN,
    FRIENDLY_NAME_OVERRIDES,
)
from .models import ValveAdvertisement


_CLACK_NAME_PREFIX = "cl_"
_LOW_SALT_CAPABLE_NAMES = {
    "CS_Meter_Soft",
    "CS_C_Meter_Soft",
    "C2_01",
    "C2_03",
    "C2_17",
    "C2_19",
    "C2_21",
    "CL_01",
    "CL_04",
    "CL_06",
    "CL_08",
}
_VALVE_ERROR_TIMEOUT_CODE = 7

_VALVE_ERROR_DISPLAY: dict[int, str] = {
    0: "No Error",
    2: "Lost home, but looking for home",
    3: "Not seeing slots, normal motor current",
    4: "Lost home, can't find it after looking",
    5: "Not seeing slots, high motor current",
    6: "Not seeing slots, no motor current",
    192: "Regen aborted, can't start a regen while on battery",
}

_VALVE_TYPE_DISPLAY: dict[int, str] = {
    0: "Unknown",
    254: "Commercial test valve",
    255: "Test valve",
}
_VALVE_TYPE_DISPLAY.update({index: f"Valve type {index:02d}" for index in range(1, 28)})

_VALVE_SERIES_EVB034_DISPLAY: dict[int, str] = {
    0: "Unknown",
    2: "Series 2",
    3: "Series 3",
    4: "Series 4",
    5: "Series 5",
    6: "Series 6",
}

_VALVE_SERIES_EBX044_DISPLAY: dict[int, str] = {
    0: "Unknown",
    1: "Series 1",
    2: "Series 2",
    3: "Series 3",
    4: "Series 4",
    5: "Series 5",
    6: "Series 6",
    7: "Series 7",
}

_SALT_SENSOR_STATUS_DISPLAY: dict[int, str] = {
    -1: "Unknown",
    0: "Salt okay",
    1: "Salt low",
}

_WATER_STATUS_DISPLAY: dict[int, str] = {
    -1: "Unknown",
    0: "Water on",
    1: "Water off",
}

_BYPASS_STATUS_DISPLAY: dict[int, str] = {
    -1: "Unknown",
    0: "Bypass off",
    1: "Bypass on",
}


def friendly_name_from_advertised_name(advertised_name: str | None) -> str:
    """Return a friendly valve name for a Bluetooth advertised local name."""

    if not advertised_name:
        return DEFAULT_FRIENDLY_NAME

    normalized_name = advertised_name.strip().casefold()
    if not normalized_name:
        return DEFAULT_FRIENDLY_NAME

    return FRIENDLY_NAME_OVERRIDES.get(normalized_name, DEFAULT_FRIENDLY_NAME)


def _is_clack_valve(advertised_name: str | None) -> bool:
    """Return ``True`` if the Bluetooth name indicates a Clack valve."""

    if not advertised_name:
        return False
    return advertised_name.strip().casefold().startswith(_CLACK_NAME_PREFIX)


def _can_report_low_salt(advertised_name: str | None) -> bool:
    """Return ``True`` if the valve can report a low salt condition."""

    if not advertised_name:
        return False

    normalized_name = advertised_name.strip()
    if not normalized_name:
        return False

    return normalized_name in _LOW_SALT_CAPABLE_NAMES


def _valve_error_display(error_code: int | None, is_clack_valve: bool) -> str | None:
    """Return the display string for a valve error enumeration value."""

    if error_code is None:
        return None
    if error_code == _VALVE_ERROR_TIMEOUT_CODE:
        return "Drive 1 motor timeout error" if is_clack_valve else "TWEDO motor timeout error"
    return _VALVE_ERROR_DISPLAY.get(error_code)


def _valve_type_display(valve_type: int | None) -> str | None:
    """Return the display string for a valve type enumeration value."""

    if valve_type is None:
        return None
    return _VALVE_TYPE_DISPLAY.get(valve_type)


def _salt_sensor_status_display(status: int | None) -> str | None:
    """Return the display string for a salt sensor status enumeration value."""

    if status is None:
        return None
    return _SALT_SENSOR_STATUS_DISPLAY.get(status)


def _water_status_display(status: int | None) -> str | None:
    """Return the display string for a water status enumeration value."""

    if status is None:
        return None
    return _WATER_STATUS_DISPLAY.get(status)


def _bypass_status_display(status: int | None) -> str | None:
    """Return the display string for a bypass status enumeration value."""

    if status is None:
        return None
    return _BYPASS_STATUS_DISPLAY.get(status)


def _valve_series_display(
    mapping: Mapping[int, str], series_value: int | None
) -> str | None:
    """Return the display string for a valve series enumeration value."""

    if series_value is None:
        return None
    return mapping.get(series_value)


def _convert_version_number_to_string(
    firmware_version: int, is_clack_valve: bool
) -> str:
    """Render a firmware version number similar to Chandler's mobile app."""

    if 100 <= firmware_version <= 199:
        prefix = "D"
    elif is_clack_valve:
        prefix = "L"
    else:
        prefix = "C"
    major = firmware_version // 100
    minor = firmware_version % 100
    return f"{prefix}{major}.{minor:02d}"


class ChandlerValveEntity(Entity):
    """Base entity shared by Chandler Legacy valve platforms."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, advertisement: ValveAdvertisement) -> None:
        """Initialize the entity."""

        self._advertisement = advertisement
        self._attr_unique_id = advertisement.address
        self._attr_name = self._compute_name(advertisement)

    @property
    def device_info(self) -> DeviceInfo:
        """Return metadata for the device registry."""

        return DeviceInfo(
            identifiers={(DOMAIN, self._advertisement.address)},
            name=self._compute_name(self._advertisement),
            manufacturer=DEFAULT_MANUFACTURER,
            model=self._advertisement.model,
            via_device=(DOMAIN, DISCOVERY_VIA_DEVICE_ID),
            sw_version=self._format_firmware_version(self._advertisement),
        )

    def async_update_from_advertisement(self, advertisement: ValveAdvertisement) -> None:
        """Store the most recent advertisement seen for this valve."""

        self._advertisement = advertisement
        self._attr_name = self._compute_name(advertisement)

    def _compute_name(self, advertisement: ValveAdvertisement) -> str:
        """Generate a user-friendly name for the valve entity."""

        return friendly_name_from_advertised_name(advertisement.name)

    def _format_firmware_version(self, advertisement: ValveAdvertisement) -> str | None:
        """Format the firmware version reported by the advertisement."""

        firmware_version = advertisement.firmware_version
        if firmware_version is None:
            if (
                advertisement.firmware_major is None
                or advertisement.firmware_minor is None
            ):
                return None
            firmware_version = (
                advertisement.firmware_major * 100 + advertisement.firmware_minor
            )

        if firmware_version < 0:
            return None

        return _convert_version_number_to_string(
            firmware_version, _is_clack_valve(advertisement.name)
        )
