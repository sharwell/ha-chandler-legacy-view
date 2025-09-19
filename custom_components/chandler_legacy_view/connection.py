"""Active Bluetooth connection management for Chandler valves."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Iterable
from datetime import datetime

from bleak.backends.client import BaseBleakClient
from bleak_retry_connector import (
    BLEAK_RETRY_EXCEPTIONS,
    BleakClientWithServiceCache,
    establish_connection,
)
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothChange
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import CONNECTION_POLL_INTERVAL, CONNECTION_TIMEOUT_SECONDS
from .discovery import BLUETOOTH_LOST_CHANGES, ValveDiscoveryManager
from .models import ValveAdvertisement

_LOGGER = logging.getLogger(__name__)


class ValveConnection:
    """Handle an active Bluetooth data poll for a valve."""

    def __init__(self, hass: HomeAssistant, address: str) -> None:
        """Initialize the valve connection handler."""

        self._hass = hass
        self._address = address
        self._advertisement: ValveAdvertisement | None = None
        self._available = False
        self._last_seen: datetime | None = None
        self._last_success: datetime | None = None
        self._lock = asyncio.Lock()
        self._unloaded = False

    @property
    def address(self) -> str:
        """Return the Bluetooth address of the valve."""

        return self._address

    @property
    def available(self) -> bool:
        """Return ``True`` if the valve is currently available for polling."""

        return self._available and not self._unloaded

    @property
    def last_success(self) -> datetime | None:
        """Return the timestamp of the last successful poll."""

        return self._last_success

    def update_from_advertisement(self, advertisement: ValveAdvertisement) -> None:
        """Record the most recent Bluetooth advertisement for the valve."""

        self._advertisement = advertisement
        self._available = True
        self._last_seen = dt_util.utcnow()

    def mark_unavailable(self) -> None:
        """Mark the valve as temporarily unavailable."""

        self._available = False

    def schedule_poll(self) -> None:
        """Schedule a background poll of the valve."""

        if not self.available:
            return
        self._hass.async_create_task(self.async_poll())

    async def async_unload(self) -> None:
        """Prevent future polls and wait for any active poll to finish."""

        self._unloaded = True
        async with self._lock:
            return

    async def async_poll(self) -> None:
        """Attempt to connect to the valve and fetch additional data."""

        if not self.available:
            return

        if self._lock.locked():
            _LOGGER.debug(
                "Skipping poll for %s; another poll is already running", self._address
            )
            return

        async with self._lock:
            await self._async_poll_locked()

    async def _async_poll_locked(self) -> None:
        """Perform a Bluetooth connection cycle for the valve."""

        advertisement = self._advertisement
        if advertisement is None:
            _LOGGER.debug(
                "Skipping poll for %s; no advertisement data is available", self._address
            )
            return

        ble_device = bluetooth.async_ble_device_from_address(
            self._hass, self._address, connectable=True
        )
        if ble_device is None:
            _LOGGER.debug(
                "Bluetooth device %s is not currently connectable", self._address
            )
            return

        _LOGGER.debug("Connecting to valve %s to refresh diagnostic data", self._address)

        try:
            async with asyncio.timeout(CONNECTION_TIMEOUT_SECONDS):
                client = await establish_connection(
                    BleakClientWithServiceCache,
                    ble_device,
                    self._address,
                )
        except asyncio.TimeoutError:
            _LOGGER.warning(
                "Timed out while attempting to connect to valve %s", self._address
            )
            return
        except BLEAK_RETRY_EXCEPTIONS as exc:
            _LOGGER.debug(
                "Unable to establish Bluetooth connection to valve %s: %s",
                self._address,
                exc,
            )
            return
        except Exception:  # pragma: no cover - unexpected errors are logged
            _LOGGER.exception(
                "Unexpected error connecting to valve %s", self._address
            )
            return

        try:
            await self._async_fetch_device_information(client)
        except Exception:  # pragma: no cover - future protocol work may raise
            _LOGGER.exception(
                "Error while retrieving extended data from valve %s", self._address
            )
        else:
            self._last_success = dt_util.utcnow()
        finally:
            with contextlib.suppress(Exception):
                await client.disconnect()

    async def _async_fetch_device_information(
        self, client: BaseBleakClient
    ) -> None:
        """Retrieve extended diagnostic information from the valve."""

        if self._advertisement is None:
            return

        _LOGGER.debug(
            "Connected to valve %s (%s); extended polling not yet implemented",
            self._address,
            self._advertisement.model or "unknown model",
        )


class ValveConnectionManager:
    """Coordinate periodic Bluetooth polling for discovered valves."""

    def __init__(
        self,
        hass: HomeAssistant,
        discovery_manager: ValveDiscoveryManager,
    ) -> None:
        """Initialize the connection manager."""

        self._hass = hass
        self._discovery_manager = discovery_manager
        self._connections: dict[str, ValveConnection] = {}
        self._remove_listener: CALLBACK_TYPE | None = None
        self._cancel_interval: CALLBACK_TYPE | None = None

    async def async_setup(self) -> None:
        """Begin tracking valves for periodic polling."""

        for advertisement in self._discovery_manager.devices.values():
            connection = self._ensure_connection(advertisement)
            connection.schedule_poll()

        self._remove_listener = self._discovery_manager.async_add_listener(
            self._handle_discovery_event
        )
        self._cancel_interval = async_track_time_interval(
            self._hass, self._handle_poll_interval, CONNECTION_POLL_INTERVAL
        )

    async def async_unload(self) -> None:
        """Cancel scheduled work and disconnect listeners."""

        if self._remove_listener is not None:
            self._remove_listener()
            self._remove_listener = None

        if self._cancel_interval is not None:
            self._cancel_interval()
            self._cancel_interval = None

        await asyncio.gather(
            *(connection.async_unload() for connection in self._connections.values()),
            return_exceptions=True,
        )
        self._connections.clear()

    @callback
    def _handle_poll_interval(self, _: datetime) -> None:
        """Poll each known valve on a fixed schedule."""

        for connection in self._connections.values():
            connection.schedule_poll()

    @callback
    def _handle_discovery_event(
        self, advertisement: ValveAdvertisement, change: BluetoothChange
    ) -> None:
        """React to Bluetooth discovery updates from the passive scanner."""

        if change in BLUETOOTH_LOST_CHANGES:
            connection = self._connections.get(advertisement.address)
            if connection is not None:
                connection.mark_unavailable()
            return

        connection = self._ensure_connection(advertisement)
        connection.schedule_poll()

    def _ensure_connection(self, advertisement: ValveAdvertisement) -> ValveConnection:
        """Return the connection handler for an advertisement's address."""

        connection = self._connections.get(advertisement.address)
        if connection is None:
            connection = ValveConnection(self._hass, advertisement.address)
            self._connections[advertisement.address] = connection
        connection.update_from_advertisement(advertisement)
        return connection

    def get_connections(self) -> Iterable[ValveConnection]:
        """Return an iterable over the tracked valve connections."""

        return self._connections.values()
