"""Bluetooth discovery support for Chandler Legacy water system valves."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Dict, Mapping

from homeassistant.components.bluetooth import (
    BluetoothChange,
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
    async_register_callback,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant

from .const import CSI_MANUFACTURER_ID, VALVE_MATCHERS, VALVE_NAME_PREFIXES
from .device_registry import async_update_device_sw_version
from .entity import _is_clack_valve, format_firmware_version
from .models import ValveAdvertisement

_LOGGER = logging.getLogger(__name__)

ValveListener = Callable[[ValveAdvertisement, BluetoothChange], None]

_VALVE_NAME_PREFIXES_CASEFOLD = tuple(
    prefix.casefold() for prefix in VALVE_NAME_PREFIXES
)

_CLACK_VALVE_TYPE_MAP: dict[int, str] = {
    1: "MeteredSoftener",
    4: "MeteredSoftener",
    6: "MeteredSoftener",
    8: "MeteredSoftener",
    2: "BackwashingFilter",
    5: "BackwashingFilter",
    7: "BackwashingFilter",
    9: "BackwashingFilter",
    3: "ClackAeration",
}

_STANDARD_VALVE_TYPE_MAP: dict[int, str] = {
    1: "MeteredSoftener",
    3: "MeteredSoftener",
    19: "MeteredSoftener",
    21: "MeteredSoftener",
    2: "TimeClockSoftener",
    4: "BackwashingFilter",
    5: "BackwashingFilter",
    6: "BackwashingFilter",
    7: "BackwashingFilter",
    20: "BackwashingFilter",
    22: "BackwashingFilter",
    26: "BackwashingFilter",
    27: "BackwashingFilter",
    8: "UltraFilter",
    9: "CenturionNitro",
    11: "CenturionNitro",
    10: "CenturionNitroSidekick",
    12: "CenturionNitroSidekick",
    13: "NitroPro",
    14: "NitroProSidekick",
    15: "NitroProSidekick",
    16: "CenturionNitroSidekickV3",
    17: "CommercialMeteredSoftener",
    18: "CommercialBackwashingFilter",
    23: "NitroFilter",
    24: "Sidekick",
    25: "CommercialAeration",
}

def _map_valve_type(value: int | None, is_clack_valve: bool) -> str | None:
    """Map a raw valve type value to the consolidated CsValveType string."""

    if value is None:
        return None

    if is_clack_valve:
        return _CLACK_VALVE_TYPE_MAP.get(value, "Unknown")

    return _STANDARD_VALVE_TYPE_MAP.get(value, "Unknown")


BLUETOOTH_LOST_CHANGES: tuple[BluetoothChange, ...] = tuple(
    getattr(BluetoothChange, change_name)
    for change_name in ("LOST", "UNAVAILABLE", "DISCONNECTED")
    if hasattr(BluetoothChange, change_name)
)

_BLUETOOTH_ADVERTISEMENT_CHANGE: BluetoothChange | None = getattr(
    BluetoothChange, "ADVERTISEMENT", None
)

_EVB019_VALVE_ERROR_MAP: dict[int, int] = {
    1: 2,
    2: 3,
    4: 4,
    8: 5,
    16: 6,
    32: 7,
}


def _matches_valve_prefix(name: str | None) -> bool:
    """Return ``True`` if the Bluetooth local name matches known prefixes."""

    if not name:
        return False
    comparison_value = name.casefold()
    return any(
        comparison_value.startswith(prefix)
        for prefix in _VALVE_NAME_PREFIXES_CASEFOLD
    )


def _flatten_manufacturer_data(value: Any) -> bytes | None:
    """Collapse a manufacturer data value into a single ``bytes`` object."""

    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)

    if isinstance(value, str):
        return value.encode()

    if isinstance(value, int):
        if 0 <= value <= 255:
            return bytes((value,))
        return None

    if isinstance(value, Mapping):
        return _flatten_manufacturer_data(value.values())

    if isinstance(value, Iterable):
        flattened = bytearray()
        for item in value:
            part = _flatten_manufacturer_data(item)
            if part is None:
                return None
            flattened.extend(part)
        return bytes(flattened)

    try:
        return bytes(value)
    except (TypeError, ValueError):
        return None


def _decode_firmware_number(value: int) -> int:
    """Decode Chandler's unusual firmware byte representation."""

    formatted = f"{value:02X}"
    try:
        return int(formatted)
    except ValueError:
        return value & 0xFF


@dataclass(slots=True)
class _ManufacturerClassification:
    """Details parsed from a Chandler manufacturer data payload."""

    is_csi_device: bool
    firmware_major: int | None = None
    firmware_minor: int | None = None
    firmware_version: int | None = None
    model: str | None = None
    is_twin_valve: bool = False
    is_400_series: bool = False
    has_connection_counter: bool = False
    valve_data_parsed: bool = False
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
    ignore_advertisement: bool = False
    authentication_required: bool = False


def _has_manufacturer_data_values(value: Any) -> bool:
    """Return ``True`` if the manufacturer data value contains at least one item."""

    if isinstance(value, (bytes, bytearray, memoryview)):
        return len(value) > 0

    if isinstance(value, str):
        return len(value) > 0

    if isinstance(value, int):
        return True

    if isinstance(value, Mapping):
        return any(_has_manufacturer_data_values(item) for item in value.values())

    if isinstance(value, Iterable):
        for item in value:
            if _has_manufacturer_data_values(item):
                return True
        return False

    return value is not None


def _extract_raw_manufacturer_segments(
    raw_advertisement: bytes | bytearray | memoryview | None,
) -> list[bytes]:
    """Return raw Chandler manufacturer segments from a Bluetooth advertisement."""

    if not raw_advertisement:
        _LOGGER.debug(
            "No raw advertisement provided while extracting manufacturer segments"
        )
        return []

    data = bytes(raw_advertisement)
    if not data:
        _LOGGER.debug(
            "Empty raw advertisement provided while extracting manufacturer segments"
        )
        return []

    index = 0
    total_length = len(data)
    prefix_le = CSI_MANUFACTURER_ID.to_bytes(2, "little")
    segments: list[bytes] = []

    while index < total_length:
        segment_length = data[index]
        index += 1
        if segment_length == 0:
            _LOGGER.debug(
                "Encountered zero-length segment at index %s while extracting manufacturer segments",
                index - 1,
            )
            break

        if index + segment_length > total_length:
            _LOGGER.debug(
                "Segment starting at index %s with length %s exceeds advertisement size %s",
                index - 1,
                segment_length,
                total_length,
            )
            break

        ad_type = data[index]
        index += 1
        payload_length = segment_length - 1
        payload_start = index
        segment_payload = data[payload_start : payload_start + payload_length]
        index += payload_length

        if ad_type != 0xFF or payload_length < 2:
            continue

        if segment_payload.startswith(prefix_le):
            segments.append(bytes(segment_payload))
            _LOGGER.debug(
                "Found Chandler manufacturer segment at index %s: %s",
                payload_start,
                segment_payload.hex(),
            )

    if index < total_length:
        _LOGGER.debug(
            "Manufacturer segment extraction stopped at index %s before processing all %s bytes",
            index,
            total_length,
        )

    return segments


def _combine_manufacturer_segments(segments: list[bytes]) -> bytes | None:
    """Collapse segmented manufacturer data into a single payload."""

    if not segments:
        return None

    first_segment = segments[0]
    if len(first_segment) < 2:
        return None

    combined = bytearray(first_segment)

    for segment in segments[1:]:
        if len(segment) < 2:
            continue
        combined.extend(segment[2:])

    return bytes(combined)


def _get_full_manufacturer_payload(
    raw_payload: Any, raw_advertisement: bytes | bytearray | memoryview | None
) -> bytes | None:
    """Return the complete Chandler manufacturer payload."""

    segments = _extract_raw_manufacturer_segments(raw_advertisement)
    return _combine_manufacturer_segments(segments)


def _classify_manufacturer_data(
    manufacturer_data: Mapping[int, bytes],
    raw_advertisement: bytes | bytearray | memoryview | None,
) -> _ManufacturerClassification:
    """Identify Chandler valves and extract firmware details from manufacturer data."""

    raw_payload = manufacturer_data.get(CSI_MANUFACTURER_ID)
    if raw_payload is None or not _has_manufacturer_data_values(raw_payload):
        return _ManufacturerClassification(False)

    payload = _get_full_manufacturer_payload(raw_payload, raw_advertisement)
    if payload is None:
        _LOGGER.debug(
            "Manufacturer data for Chandler valve (id %s) had unexpected structure: %s",
            CSI_MANUFACTURER_ID,
            raw_payload,
        )
        return _ManufacturerClassification(True, ignore_advertisement=True)

    prefix_le = CSI_MANUFACTURER_ID.to_bytes(2, "little")
    if not payload.startswith(prefix_le):
        _LOGGER.debug(
            "Manufacturer data for Chandler valve (id %s) did not start with expected prefix: %s",
            CSI_MANUFACTURER_ID,
            payload,
        )
        return _ManufacturerClassification(True)

    if len(payload) < 4:
        _LOGGER.debug(
            "Manufacturer data for Chandler valve (id %s) was too short to parse firmware: %s",
            CSI_MANUFACTURER_ID,
            payload,
        )
        return _ManufacturerClassification(True)

    firmware_major_raw = payload[-2]
    firmware_minor_raw = payload[-1]

    firmware_major = _decode_firmware_number(firmware_major_raw)
    firmware_minor_converted = _decode_firmware_number(firmware_minor_raw)
    firmware_minor = 99 if firmware_minor_converted >= 250 else firmware_minor_converted
    firmware_version = firmware_major * 100 + firmware_minor
    model: str | None
    if firmware_version >= 600:
        model = "Evb034"
    else:
        model = "Evb019"

    classification = _ManufacturerClassification(
        True,
        firmware_major,
        firmware_minor,
        firmware_version,
        model,
    )

    classification.is_twin_valve = 100 <= firmware_version <= 199
    classification.is_400_series = 400 <= firmware_version <= 499

    classification.has_connection_counter = classification.is_twin_valve or (
        classification.firmware_version is not None
        and classification.firmware_version >= 412
    )

    if classification.model == "Evb034":
        _parse_evb034_payload(payload, classification)
    else:
        _parse_evb019_payload(payload, classification)

    return classification


def _apply_valve_status(
    classification: _ManufacturerClassification, valve_status: int
) -> None:
    """Populate salt, water and bypass status flags from the valve status bits."""

    classification.valve_status = valve_status
    if classification.model == "Evb019":
        classification.authentication_required = bool(valve_status & 0x01)
        classification.salt_sensor_status = 1 if valve_status & 0x02 else 0
        classification.water_status = 1 if valve_status & 0x04 else 0
        classification.bypass_status = 1 if valve_status & 0x08 else 0
    else:
        classification.authentication_required = False
        classification.salt_sensor_status = 1 if valve_status & 0x01 else 0
        classification.water_status = 1 if valve_status & 0x02 else 0
        classification.bypass_status = 1 if valve_status & 0x04 else 0


def _parse_evb034_payload(
    payload: bytes, classification: _ManufacturerClassification
) -> None:
    """Parse an Evb034 advertisement payload."""

    if len(payload) < 10:
        return

    prefix_le = CSI_MANUFACTURER_ID.to_bytes(2, "little")
    if payload[0:2] != prefix_le:
        return

    classification.valve_data_parsed = True
    valve_status = payload[2]
    _apply_valve_status(classification, valve_status)
    classification.valve_error = payload[3]
    classification.valve_time_hours = payload[4]
    classification.valve_time_minutes = payload[5]
    classification.valve_type_full = payload[6]
    classification.valve_series_version = payload[7]


def _parse_evb019_payload(
    payload: bytes, classification: _ManufacturerClassification
) -> None:
    """Parse an Evb019 advertisement payload."""

    if len(payload) < 6:
        return

    prefix_le = CSI_MANUFACTURER_ID.to_bytes(2, "little")
    if payload[0:2] != prefix_le:
        return

    has_connection_counter = classification.has_connection_counter
    has_minimum_payload = len(payload) >= 8
    has_required_length = (not has_connection_counter) or len(payload) >= 14
    twin_valve_valid = (not classification.is_twin_valve) or (
        len(payload) >= 8 and payload[7] == 100
    )

    parsed = has_minimum_payload and has_required_length and twin_valve_valid
    if not parsed:
        classification.valve_data_parsed = False
        return

    classification.valve_data_parsed = True
    valve_status = payload[2]
    _apply_valve_status(classification, valve_status)
    raw_valve_error = payload[3]
    classification.valve_error = _EVB019_VALVE_ERROR_MAP.get(raw_valve_error, 0)
    classification.valve_time_hours = payload[4]
    classification.valve_time_minutes = payload[5]

    if has_connection_counter:
        if len(payload) > 6:
            classification.connection_counter = payload[6]
        if len(payload) > 8:
            classification.bootloader_version = payload[8]
        if len(payload) > 9:
            classification.valve_series_version = payload[9]
        if len(payload) > 10:
            classification.radio_protocol_version = payload[10]
        if len(payload) > 11:
            classification.valve_type_full = payload[11]
    else:
        if len(payload) > 6:
            classification.bootloader_version = payload[6]
        if len(payload) > 7:
            classification.valve_series_version = payload[7]
        if len(payload) == 12:
            if len(payload) > 8:
                classification.radio_protocol_version = payload[8]
            if len(payload) > 9:
                classification.valve_type_full = payload[9]
        elif len(payload) > 8:
            classification.valve_type_full = payload[8]

class ValveDiscoveryManager:
    """Track Bluetooth advertisements originating from known valves."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the manager."""

        self._hass = hass
        self._callbacks: list[CALLBACK_TYPE] = []
        self._listeners: list[ValveListener] = []
        self._devices: Dict[str, ValveAdvertisement] = {}

    async def async_setup(self) -> None:
        """Start listening for Bluetooth advertisements."""

        _LOGGER.debug("Setting up Bluetooth discovery for Chandler valves")
        for matcher in VALVE_MATCHERS:
            self._callbacks.append(
                async_register_callback(
                    self._hass,
                    self._async_handle_bluetooth_event,
                    matcher,
                    BluetoothScanningMode.PASSIVE,
                )
            )

    async def async_unload(self) -> None:
        """Cancel Bluetooth callbacks and clear tracked devices."""

        _LOGGER.debug("Unloading Bluetooth discovery for Chandler valves")
        while self._callbacks:
            remove = self._callbacks.pop()
            remove()
        self._listeners.clear()
        self._devices.clear()

    @property
    def devices(self) -> Dict[str, ValveAdvertisement]:
        """Return a snapshot of the tracked devices."""

        return dict(self._devices)

    def async_add_listener(self, listener: ValveListener) -> CALLBACK_TYPE:
        """Register a listener that is notified when a valve advertisement is seen."""

        self._listeners.append(listener)

        def _remove_listener() -> None:
            with contextlib.suppress(ValueError):
                self._listeners.remove(listener)

        return _remove_listener

    def _async_handle_bluetooth_event(
        self, service_info: BluetoothServiceInfoBleak, change: BluetoothChange
    ) -> None:
        """Handle an incoming Bluetooth advertisement from Home Assistant."""

        if change in BLUETOOTH_LOST_CHANGES:
            advertisement = self._devices.pop(service_info.address, None)
            if advertisement is None:
                _LOGGER.debug(
                    "Ignoring lost event for %s; device was not tracked as a valve",
                    service_info.address,
                )
                return
            _LOGGER.debug("Valve %s lost", service_info.address)
        elif change is _BLUETOOTH_ADVERTISEMENT_CHANGE:
            if not _matches_valve_prefix(service_info.name):
                _LOGGER.debug(
                    "Ignoring Bluetooth advertisement from %s with name %r",
                    service_info.address,
                    service_info.name,
                )
                return

            classification = _classify_manufacturer_data(
                service_info.manufacturer_data,
                getattr(service_info, "raw", None),
            )

            if classification.ignore_advertisement:
                _LOGGER.debug(
                    "Ignoring Bluetooth advertisement from %s; manufacturer data was incomplete",
                    service_info.address,
                )
                return

            if not classification.is_csi_device:
                _LOGGER.debug(
                    "Ignoring Bluetooth advertisement from %s; manufacturer data %s does not match Chandler signature",
                    service_info.address,
                    service_info.manufacturer_data,
                )
                return

            if (
                (
                    classification.firmware_version is not None
                    and classification.firmware_version >= 412
                )
                or classification.is_twin_valve
            ) and not classification.valve_data_parsed:
                _LOGGER.debug(
                    "Ignoring Bluetooth advertisement from %s; firmware %s requires parsed valve data",
                    service_info.address,
                    classification.firmware_version
                    if classification.firmware_version is not None
                    else "unknown",
                )
                return

            is_clack_valve = _is_clack_valve(service_info.name)
            classification.valve_type = _map_valve_type(
                classification.valve_type_full, is_clack_valve
            )

            advertisement = ValveAdvertisement(
                address=service_info.address,
                name=service_info.name,
                rssi=service_info.rssi,
                manufacturer_data=service_info.manufacturer_data,
                service_data=service_info.service_data,
                firmware_major=classification.firmware_major,
                firmware_minor=classification.firmware_minor,
                firmware_version=classification.firmware_version,
                model=classification.model,
                is_twin_valve=classification.is_twin_valve,
                is_400_series=classification.is_400_series,
                has_connection_counter=classification.has_connection_counter,
                valve_data_parsed=classification.valve_data_parsed,
                valve_status=classification.valve_status,
                salt_sensor_status=classification.salt_sensor_status,
                water_status=classification.water_status,
                bypass_status=classification.bypass_status,
                authentication_required=classification.authentication_required,
                valve_error=classification.valve_error,
                valve_time_hours=classification.valve_time_hours,
                valve_time_minutes=classification.valve_time_minutes,
                valve_type_full=classification.valve_type_full,
                valve_type=classification.valve_type,
                valve_series_version=classification.valve_series_version,
                connection_counter=classification.connection_counter,
                bootloader_version=classification.bootloader_version,
                radio_protocol_version=classification.radio_protocol_version,
            )
            async_update_device_sw_version(
                self._hass,
                advertisement.address,
                format_firmware_version(advertisement),
            )
            self._devices[service_info.address] = advertisement
            if classification.firmware_version is not None:
                _LOGGER.debug(
                    "Valve %s seen (RSSI=%s, firmware=%s)",
                    service_info.address,
                    service_info.rssi,
                    classification.firmware_version,
                )
            else:
                _LOGGER.debug(
                    "Valve %s seen (RSSI=%s)",
                    service_info.address,
                    service_info.rssi,
                )
        else:
            _LOGGER.debug(
                "Ignoring Bluetooth change %s for %s", change, service_info.address
            )
            return

        for listener in list(self._listeners):
            listener(advertisement, change)
