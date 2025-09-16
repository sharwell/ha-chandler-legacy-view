"""Bluetooth discovery support for Chandler Legacy water system valves."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Dict

from homeassistant.components.bluetooth import (
    BluetoothChange,
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
    async_register_callback,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant

from .const import VALVE_MATCHERS, VALVE_NAME_PREFIXES
from .models import ValveAdvertisement

_LOGGER = logging.getLogger(__name__)

ValveListener = Callable[[ValveAdvertisement, BluetoothChange], None]

_VALVE_NAME_PREFIXES_CASEFOLD = tuple(
    prefix.casefold() for prefix in VALVE_NAME_PREFIXES
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
        self._callbacks.append(
            async_register_callback(
                self._hass,
                self._async_handle_bluetooth_event,
                VALVE_MATCHERS,
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

        if change is BluetoothChange.LOST:
            advertisement = self._devices.pop(service_info.address, None)
            if advertisement is None:
                _LOGGER.debug(
                    "Ignoring lost event for %s; device was not tracked as a valve",
                    service_info.address,
                )
                return
            _LOGGER.debug("Valve %s lost", service_info.address)
        else:
            if not _matches_valve_prefix(service_info.name):
                _LOGGER.debug(
                    "Ignoring Bluetooth advertisement from %s with name %r",
                    service_info.address,
                    service_info.name,
                )
                return

            advertisement = ValveAdvertisement(
                address=service_info.address,
                name=service_info.name,
                rssi=service_info.rssi,
                manufacturer_data=service_info.manufacturer_data,
                service_data=service_info.service_data,
            )
            self._devices[service_info.address] = advertisement
            _LOGGER.debug(
                "Valve %s seen (RSSI=%s)",
                service_info.address,
                service_info.rssi,
            )

        for listener in list(self._listeners):
            listener(advertisement, change)
