"""Bluetooth discovery support for Chandler Legacy water system valves."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Dict, Mapping

from homeassistant.components.bluetooth import (
    BluetoothChange,
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
    async_register_callback,
    async_track_unavailable,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant

from .const import CSI_MANUFACTURER_ID, VALVE_MATCHERS, VALVE_NAME_PREFIXES
from .device_registry import async_update_device_sw_version
from .entity import _is_clack_valve, format_firmware_version
from .models import ValveAdvertisement

_LOGGER = logging.getLogger(__name__)


class ValveDiscoveryChange(Enum):
    """Availability updates from advertisements or an active data session."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


ValveListener = Callable[[ValveAdvertisement, ValveDiscoveryChange], None]


def _merge_incomplete_advertisement(
    previous: ValveAdvertisement, current: ValveAdvertisement
) -> ValveAdvertisement:
    """Merge a newly received incomplete advertisement with previous data."""

    return replace(
        previous,
        address=current.address,
        name=current.name,
        rssi=current.rssi,
        manufacturer_data=current.manufacturer_data,
        service_data=current.service_data,
        manufacturer_data_complete=current.manufacturer_data_complete,
        valve_data_parsed=current.valve_data_parsed,
    )


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
    ignore_advertisement: bool = False
    authentication_required: bool | None = None


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
            return []

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
        else:
            other_manufacturer = int.from_bytes(
                segment_payload[:2], "little", signed=False
            )
            _LOGGER.debug(
                "Skipping manufacturer segment at index %s for manufacturer %s (expected %s): %s",
                payload_start,
                other_manufacturer,
                CSI_MANUFACTURER_ID,
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
    """Normalize one observation without mixing raw and cached manufacturer data."""

    if raw_advertisement is not None:
        if not isinstance(raw_advertisement, (bytes, bytearray, memoryview)):
            return None
        # Home Assistant can retain older manufacturer records. If raw data is
        # available, only segments from this advertisement describe this update.
        segments = _extract_raw_manufacturer_segments(raw_advertisement)
        return _combine_manufacturer_segments(segments)

    if not isinstance(raw_payload, (bytes, bytearray, memoryview)) or not raw_payload:
        return None
    # BluetoothServiceInfoBleak keys structured data by company identifier and
    # excludes that identifier from the corresponding bytes.
    return CSI_MANUFACTURER_ID.to_bytes(2, "little") + bytes(raw_payload)


def _classify_manufacturer_data(
    manufacturer_data: Mapping[int, bytes],
    raw_advertisement: bytes | bytearray | memoryview | None,
) -> _ManufacturerClassification:
    """Identify Chandler valves and extract firmware details from manufacturer data."""

    raw_payload = manufacturer_data.get(CSI_MANUFACTURER_ID)
    payload = _get_full_manufacturer_payload(raw_payload, raw_advertisement)
    if payload is None:
        _LOGGER.debug(
            "Manufacturer data for Chandler valve (id %s) had unexpected structure: %s",
            CSI_MANUFACTURER_ID,
            raw_payload,
        )
        return _ManufacturerClassification(True, manufacturer_data_complete=False)

    prefix_le = CSI_MANUFACTURER_ID.to_bytes(2, "little")
    if not payload.startswith(prefix_le):
        _LOGGER.debug(
            "Manufacturer data for Chandler valve (id %s) did not start with expected prefix: %s",
            CSI_MANUFACTURER_ID,
            payload,
        )
        return _ManufacturerClassification(True, manufacturer_data_complete=False)

    # Short status/counter packets do not contain firmware. In particular their
    # final zero bytes must never be interpreted as a complete C0.00 record.
    # These lengths include the two-byte company identifier.
    if len(payload) not in (10, 11, 12, 14):
        _LOGGER.debug(
            "Partial or unsupported Chandler manufacturer layout (id %s, length %s): %s",
            CSI_MANUFACTURER_ID,
            len(payload),
            payload.hex(),
        )
        return _ManufacturerClassification(True, manufacturer_data_complete=False)

    firmware_major_raw = payload[-2]
    firmware_minor_raw = payload[-1]

    firmware_major = _decode_firmware_number(firmware_major_raw)
    firmware_minor_converted = _decode_firmware_number(firmware_minor_raw)
    firmware_minor = 99 if firmware_minor_converted >= 250 else firmware_minor_converted
    firmware_version = firmware_major * 100 + firmware_minor
    is_twin_valve = 100 <= firmware_version <= 199
    has_connection_counter = is_twin_valve or firmware_version >= 412
    if firmware_version >= 600:
        model = "Evb034"
        valid_layout = len(payload) == 10
    elif has_connection_counter:
        model = "Evb019"
        valid_layout = len(payload) == 14 and (
            not is_twin_valve or payload[7] == 100
        )
    else:
        model = "Evb019"
        # The legacy layout carries bootloader, series, valve type, and the
        # final firmware pair; its longer variant also carries radio version.
        valid_layout = len(payload) in (11, 12)

    if firmware_version == 0 or not valid_layout:
        _LOGGER.debug(
            "Unsupported Chandler manufacturer layout (length %s); firmware and capabilities remain unknown: %s",
            len(payload),
            payload.hex(),
        )
        return _ManufacturerClassification(True, manufacturer_data_complete=False)

    classification = _ManufacturerClassification(
        True,
        firmware_major,
        firmware_minor,
        firmware_version,
        model,
    )

    classification.is_twin_valve = is_twin_valve
    classification.is_400_series = 400 <= firmware_version <= 499

    classification.has_connection_counter = has_connection_counter

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
        classification.salt_sensor_status = 1 if valve_status & 0x80 else 0
        classification.water_status = 1 if valve_status & 0x40 else 0
        classification.bypass_status = 1 if valve_status & 0x20 else 0


def _parse_evb034_payload(
    payload: bytes, classification: _ManufacturerClassification
) -> None:
    """Parse an Evb034 advertisement payload."""

    if len(payload) < 10:
        classification.manufacturer_data_complete = False
        return

    prefix_le = CSI_MANUFACTURER_ID.to_bytes(2, "little")
    if payload[0:2] != prefix_le:
        classification.manufacturer_data_complete = False
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
        classification.manufacturer_data_complete = False
        return

    prefix_le = CSI_MANUFACTURER_ID.to_bytes(2, "little")
    if payload[0:2] != prefix_le:
        classification.manufacturer_data_complete = False
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
        classification.manufacturer_data_complete = False
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

    def __init__(self, hass: HomeAssistant, config_entry_id: str) -> None:
        """Initialize the manager."""

        self._hass = hass
        self._config_entry_id = config_entry_id
        self._callbacks: list[CALLBACK_TYPE] = []
        self._unavailable_callbacks: dict[str, CALLBACK_TYPE] = {}
        self._listeners: list[ValveListener] = []
        # Keep metadata through absence so partial advertisements can recover it.
        self._devices: Dict[str, ValveAdvertisement] = {}
        self._unavailable_addresses: set[str] = set()
        self._connected_addresses: set[str] = set()
        self._unloaded = False

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
        self._unloaded = True
        while self._callbacks:
            remove = self._callbacks.pop()
            remove()
        while self._unavailable_callbacks:
            _, remove = self._unavailable_callbacks.popitem()
            remove()
        self._listeners.clear()
        self._devices.clear()
        self._unavailable_addresses.clear()
        self._connected_addresses.clear()

    @property
    def devices(self) -> Dict[str, ValveAdvertisement]:
        """Return a snapshot of the currently reachable devices."""

        return {
            address: advertisement
            for address, advertisement in self._devices.items()
            if self._is_available(address)
        }

    def _is_available(self, address: str) -> bool:
        """Combine scanner reachability with the active data session."""

        return address in self._devices and (
            address not in self._unavailable_addresses
            or address in self._connected_addresses
        )

    def async_set_connection_state(self, address: str, connected: bool) -> None:
        """Keep a live session available even when the valve stops advertising."""

        if self._unloaded or address not in self._devices:
            return

        was_available = self._is_available(address)
        if connected:
            self._connected_addresses.add(address)
        else:
            self._connected_addresses.discard(address)

        available = self._is_available(address)
        if available != was_available:
            self._notify_listeners(
                self._devices[address],
                ValveDiscoveryChange.AVAILABLE
                if available
                else ValveDiscoveryChange.UNAVAILABLE,
            )

    def _async_handle_unavailable(self, service_info: BluetoothServiceInfoBleak) -> None:
        """Handle loss across all connectable Bluetooth controllers."""

        address = service_info.address
        if self._unloaded or address not in self._devices:
            return

        was_available = self._is_available(address)
        self._unavailable_addresses.add(address)
        if was_available and not self._is_available(address):
            self._notify_listeners(
                self._devices[address], ValveDiscoveryChange.UNAVAILABLE
            )

    def _notify_listeners(
        self, advertisement: ValveAdvertisement, change: ValveDiscoveryChange
    ) -> None:
        """Publish the latest metadata and effective availability."""

        for listener in list(self._listeners):
            try:
                listener(advertisement, change)
            except Exception:
                _LOGGER.exception(
                    "Error publishing valve %s availability change %s",
                    advertisement.address,
                    change.value,
                )

    def async_add_listener(self, listener: ValveListener) -> CALLBACK_TYPE:
        """Register a listener for valve metadata and availability changes."""

        self._listeners.append(listener)

        def _remove_listener() -> None:
            with contextlib.suppress(ValueError):
                self._listeners.remove(listener)

        return _remove_listener

    def _async_handle_bluetooth_event(
        self, service_info: BluetoothServiceInfoBleak, change: BluetoothChange
    ) -> None:
        """Handle an incoming Bluetooth advertisement from Home Assistant."""

        if self._unloaded:
            return

        if change is BluetoothChange.ADVERTISEMENT:
            if not _matches_valve_prefix(service_info.name):
                _LOGGER.debug(
                    "Ignoring Bluetooth advertisement from %s with name %r",
                    service_info.address,
                    service_info.name,
                )
                return

            raw_advertisement = getattr(service_info, "raw", None)
            # Keep an invalid-present value distinct from an absent raw record:
            # conversion failure must not enable fallback to cached metadata.
            raw_for_classification = raw_advertisement
            if raw_advertisement is None:
                _LOGGER.debug(
                    "Valve-like advertisement from %s with name %r had no raw payload",
                    service_info.address,
                    service_info.name,
                )
            else:
                try:
                    raw_bytes = bytes(raw_advertisement)
                except (TypeError, ValueError):
                    _LOGGER.debug(
                        "Valve-like advertisement from %s with name %r provided raw payload of unexpected type %s",
                        service_info.address,
                        service_info.name,
                        type(raw_advertisement).__name__,
                    )
                else:
                    raw_for_classification = raw_bytes
                    if raw_bytes:
                        _LOGGER.debug(
                            "Valve-like advertisement from %s with name %r had raw payload: %s",
                            service_info.address,
                            service_info.name,
                            raw_bytes.hex(),
                        )
                    else:
                        _LOGGER.debug(
                            "Valve-like advertisement from %s with name %r had an empty raw payload",
                            service_info.address,
                            service_info.name,
                        )

            classification = _classify_manufacturer_data(
                service_info.manufacturer_data,
                raw_for_classification,
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
                manufacturer_data_complete=classification.manufacturer_data_complete,
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
            previous_advertisement = self._devices.get(service_info.address)
            if (
                previous_advertisement is not None
                and not advertisement.manufacturer_data_complete
            ):
                advertisement = _merge_incomplete_advertisement(
                    previous_advertisement, advertisement
                )
            async_update_device_sw_version(
                self._hass,
                self._config_entry_id,
                advertisement.address,
                format_firmware_version(advertisement),
            )
            self._devices[service_info.address] = advertisement
            self._unavailable_addresses.discard(service_info.address)
            if service_info.address not in self._unavailable_callbacks:
                self._unavailable_callbacks[service_info.address] = (
                    async_track_unavailable(
                        self._hass,
                        self._async_handle_unavailable,
                        service_info.address,
                        connectable=True,
                    )
                )
            _LOGGER.debug(
                "Valve %s seen (RSSI=%s, effective firmware=%s, manufacturer data=%s)",
                service_info.address,
                service_info.rssi,
                advertisement.firmware_version
                if advertisement.firmware_version is not None else "unknown",
                "complete" if classification.manufacturer_data_complete else "partial",
            )
        else:
            _LOGGER.debug(
                "Ignoring Bluetooth change %s for %s", change, service_info.address
            )
            return

        self._notify_listeners(advertisement, ValveDiscoveryChange.AVAILABLE)
