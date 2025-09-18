"""Bluetooth discovery support for Chandler Legacy water system valves."""

from __future__ import annotations

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
from .models import ValveAdvertisement

_LOGGER = logging.getLogger(__name__)

ValveListener = Callable[[ValveAdvertisement, BluetoothChange], None]

_VALVE_NAME_PREFIXES_CASEFOLD = tuple(
    prefix.casefold() for prefix in VALVE_NAME_PREFIXES
)

BLUETOOTH_LOST_CHANGES: tuple[BluetoothChange, ...] = tuple(
    getattr(BluetoothChange, change_name)
    for change_name in ("LOST", "UNAVAILABLE", "DISCONNECTED")
    if hasattr(BluetoothChange, change_name)
)

_BLUETOOTH_ADVERTISEMENT_CHANGE: BluetoothChange | None = getattr(
    BluetoothChange, "ADVERTISEMENT", None
)


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


@dataclass(slots=True)
class _ManufacturerClassification:
    """Details parsed from a Chandler manufacturer data payload."""

    is_csi_device: bool
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


def _classify_manufacturer_data(
    manufacturer_data: Mapping[int, bytes]
) -> _ManufacturerClassification:
    """Identify Chandler valves and extract firmware details from manufacturer data."""

    raw_payload = manufacturer_data.get(CSI_MANUFACTURER_ID)
    if raw_payload is None:
        return _ManufacturerClassification(False)

    payload = _flatten_manufacturer_data(raw_payload)
    if payload is None:
        _LOGGER.debug(
            "Manufacturer data for Chandler valve (id %s) had unexpected structure: %s",
            CSI_MANUFACTURER_ID,
            raw_payload,
        )
        return _ManufacturerClassification(True)

    if len(payload) < 2:
        _LOGGER.debug(
            "Manufacturer data for Chandler valve (id %s) was too short to parse firmware: %s",
            CSI_MANUFACTURER_ID,
            payload,
        )
        return _ManufacturerClassification(True)

    firmware_major = payload[-2]
    firmware_minor_raw = payload[-1]
    firmware_minor = 99 if firmware_minor_raw >= 250 else firmware_minor_raw
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

    if len(payload) >= 10 and payload[0:2] == b"\x07\x3a":
        valve_status = payload[2]
        classification.valve_status = valve_status
        classification.salt_sensor_status = 1 if valve_status & 0x80 else 0
        classification.water_status = 1 if valve_status & 0x40 else 0
        classification.bypass_status = 1 if valve_status & 0x20 else 0
        classification.valve_error = payload[3]
        classification.valve_time_hours = payload[4]
        classification.valve_time_minutes = payload[5]
        classification.valve_type = payload[6]
        classification.valve_series_version = payload[7]

    return classification


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
                service_info.manufacturer_data
            )

            if not classification.is_csi_device:
                _LOGGER.debug(
                    "Ignoring Bluetooth advertisement from %s; manufacturer data %s does not match Chandler signature",
                    service_info.address,
                    service_info.manufacturer_data,
                )
                return

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
                valve_status=classification.valve_status,
                salt_sensor_status=classification.salt_sensor_status,
                water_status=classification.water_status,
                bypass_status=classification.bypass_status,
                valve_error=classification.valve_error,
                valve_time_hours=classification.valve_time_hours,
                valve_time_minutes=classification.valve_time_minutes,
                valve_type=classification.valve_type,
                valve_series_version=classification.valve_series_version,
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
