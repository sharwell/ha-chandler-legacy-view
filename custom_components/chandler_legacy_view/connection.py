"""Active Bluetooth connection management for Chandler valves."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum, IntEnum
from random import SystemRandom

from bleak.backends.client import BaseBleakClient
from bleak_retry_connector import (
    BLEAK_RETRY_EXCEPTIONS,
    BleakClientWithServiceCache,
    establish_connection,
)
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CALLBACK_TYPE, CoreState, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    BLE_OPERATION_TIMEOUT_SECONDS,
    CONF_DEFAULT_PASSCODE,
    CONF_DEVICE_PASSCODES,
    CONNECTION_MIN_RETRY_INTERVAL,
    CONNECTION_POLL_INTERVAL,
    CONNECTION_TIMEOUT_SECONDS,
    DATA_AGE_NONE,
    DATA_AGE_OLDEST,
    DATA_AGE_TIERS,
    DEFAULT_PERSISTENT_POLL_INTERVAL_SECONDS,
    DEFAULT_VALVE_PASSCODE,
    HEAL_POLL_MIN_INTERVAL_SECONDS,
    MAX_PERSISTENT_POLL_INTERVAL_SECONDS,
    MIN_PERSISTENT_POLL_INTERVAL_SECONDS,
    PERSISTENT_RECONNECT_DELAY_SECONDS,
    PERSISTENT_REQUEST_TIMEOUT_SECONDS,
    POLL_FETCH_TIMEOUT_SECONDS,
    POLL_HARD_LIMIT_SECONDS,
    STALE_AFTER_ON_DEMAND_SECONDS,
    STALE_AFTER_PERSISTENT_INTERVALS,
    STALE_AFTER_PERSISTENT_MIN_SECONDS,
    UNLOAD_TIMEOUT_SECONDS,
    WATCHDOG_INTERVAL,
)
from .device_registry import async_update_device_serial_number
from .discovery import ValveDiscoveryChange, ValveDiscoveryManager
from .models import ValveAdvertisement, ValveDashboardData

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class _ValveGattProfile:
    """Describe the expected BLE services for an EVB019 valve."""

    service_uuid: str
    notify_char_uuid: str
    write_char_uuid: str


_EVB019_GATT_PROFILES: tuple[_ValveGattProfile, ...] = (
    _ValveGattProfile(
        service_uuid="00001000-0000-1000-8000-00805f9b34fb",
        notify_char_uuid="00001002-0000-1000-8000-00805f9b34fb",
        write_char_uuid="00001001-0000-1000-8000-00805f9b34fb",
    ),
    _ValveGattProfile(
        service_uuid="6e400001-b5a3-f393-e0a9-e50e24dcca9e",
        notify_char_uuid="6e400003-b5a3-f393-e0a9-e50e24dcca9e",
        write_char_uuid="6e400002-b5a3-f393-e0a9-e50e24dcca9e",
    ),
    _ValveGattProfile(
        service_uuid="a725458c-bee1-4d2e-9555-edf5a8082303",
        notify_char_uuid="a725458c-bee2-4d2e-9555-edf5a8082303",
        write_char_uuid="a725458c-bee3-4d2e-9555-edf5a8082303",
    ),
)


class ValveRequestCommand(IntEnum):
    """Known EVB019 request opcodes."""

    RESET = 114
    SETTINGS = 121
    DEVICE_LIST = 116
    DASHBOARD = 117
    ADVANCED_SETTINGS = 118
    STATUS_AND_HISTORY = 119
    DEALER_INFORMATION = 120


_EVB019_REQUEST_PACKET_LENGTH = 20
_DEVICE_LIST_RESPONSE_TIMEOUT_SECONDS = 5
_DEVICE_LIST_MIN_RESPONSE_LENGTH = 18
_DASHBOARD_RESPONSE_TIMEOUT_SECONDS = 5
_RESET_BUFFER_TIMEOUT_SECONDS = 5
_DISCONNECT_TIMEOUT_SECONDS = 30
_DASHBOARD_PACKET_COUNT = 6
_DEFAULT_SERIAL_NUMBER = "FFFFFFFF"
_MAX_AUTHENTICATION_ATTEMPTS = 4


_CRC_RANDOM = SystemRandom()
_CRC_ALLOWED_POLYNOMIALS: tuple[int, ...] = tuple(
    polynomial
    for polynomial in range(1, 256)
    if 4 <= int.bit_count(polynomial) <= 5
)


async def _async_wait_for_cleanup(
    task: asyncio.Task[None], timeout: float | None = None
) -> None:
    """Finish cleanup before propagating cancellation of its caller.

    With ``timeout`` the wait is bounded: cleanup that has not finished by then
    is cancelled and abandoned so a stalled Bluetooth call cannot block unload.
    """

    cancelled: asyncio.CancelledError | None = None
    deadline = None if timeout is None else asyncio.get_running_loop().time() + timeout
    try:
        while not task.done():
            remaining = None
            if deadline is not None:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    _LOGGER.warning(
                        "Abandoning Bluetooth cleanup that did not finish within "
                        "%g seconds",
                        timeout,
                    )
                    task.cancel()
                    return
            try:
                await asyncio.wait({task}, timeout=remaining)
            except asyncio.CancelledError as exc:
                # Keep a strong reference and drain the same task, even when
                # shutdown requests cancellation more than once.
                cancelled = exc
        task.result()
    finally:
        if cancelled is not None:
            raise cancelled


async def _async_bounded(awaitable, timeout: float, description: str):
    """Await a Bluetooth call, raising ``TimeoutError`` if it stalls.

    Bluetooth stacks and proxies can leave a GATT call pending forever when the
    link dies silently. Bounding each call turns that into an ordinary failure
    that callers already handle by disconnecting and retrying.
    """

    try:
        async with asyncio.timeout(timeout):
            return await awaitable
    except TimeoutError:
        _LOGGER.warning(
            "Timed out after %g seconds waiting for %s", timeout, description
        )
        raise


class _ChandlerCrc8:
    """Reproduce the CRC8 helper used by the mobile application."""

    def __init__(self) -> None:
        """Initialize the CRC helper with a default configuration."""

        self._polynomial = 0
        self._seed = 0

    def set_options(self, polynomial: int, seed: int) -> None:
        """Configure the CRC calculation parameters."""

        self._polynomial = polynomial & 0xFF
        self._seed = seed & 0xFF

    def compute(self, value: int) -> int:
        """Return the CRC8 value for ``value`` using the configured options."""

        crc = (self._seed ^ (value & 0xFF)) & 0xFF
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ self._polynomial) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
        self._seed = crc
        return crc

    def compute_legacy(self, value: int) -> int:
        """Return the legacy CRC8 value for ``value`` using the configured options."""

        data = value & 0xFF
        seed = self._seed & 0xFF
        for _ in range(8):
            seed_has_high_bit = seed & 0x80
            seed = (seed << 1) & 0xFF
            if data & 0x80:
                seed = (seed | 0x01) & 0xFF
            data = (data << 1) & 0xFF
            if seed_has_high_bit:
                seed ^= self._polynomial
        self._seed = seed & 0xFF
        return self._seed


class ValveAuthenticationState(IntEnum):
    """Authentication result associated with a decoded passcode."""

    UNKNOWN = -1
    NOT_AUTHENTICATED = 0
    AUTHENTICATED = 128

    @classmethod
    def from_status(cls, value: int) -> "ValveAuthenticationState":
        """Return the authentication state encoded in the status byte."""

        if value == cls.NOT_AUTHENTICATED:
            return cls.NOT_AUTHENTICATED
        if value == cls.AUTHENTICATED:
            return cls.AUTHENTICATED
        return cls.UNKNOWN


class ValveAuthenticationProtocol(Enum):
    """Protocol learned from validated metadata or a DeviceList challenge."""

    UNKNOWN = "unknown"
    CLASSIC = "classic"
    COUNTER = "counter"


class ValvePasswordDecodeState(Enum):
    """State machine describing the result of a passcode decode attempt."""

    UNKNOWN = "unknown"
    CLASSIC = "classic"
    VALID = "valid"
    INVALID = "invalid"
    RETRY = "retry"
    RECOVERED = "recovered"
    RECOVERY_FAILED = "recovery_failed"
    AUTH_NEEDED = "auth_needed"


@dataclass(slots=True)
class ValveDecodedPassword:
    """Decoded passcode information reported by a DeviceList packet."""

    state: ValvePasswordDecodeState
    authentication_state: ValveAuthenticationState
    authentication_required: bool
    passcode: str


@dataclass(slots=True)
class ValvePasscodeConfiguration:
    """Configured passcode information for a valve."""

    value: str | None
    is_override: bool


class ValveConnection:
    """Handle an active Bluetooth data poll for a valve."""

    def __init__(
        self,
        hass: HomeAssistant,
        address: str,
        config_entry_id: str,
        passcode_getter: Callable[[str], ValvePasscodeConfiguration] | None = None,
        connection_state_callback: Callable[[str, bool], None] | None = None,
    ) -> None:
        """Initialize the valve connection handler."""

        self._hass = hass
        self._address = address
        self._config_entry_id = config_entry_id
        self._advertisement: ValveAdvertisement | None = None
        self._available = False
        self._last_seen: datetime | None = None
        self._last_success: datetime | None = None
        self._lock = asyncio.Lock()
        self._unloaded = False
        self._next_connection_time: datetime | None = None
        self._cooldown_cancel: CALLBACK_TYPE | None = None
        self._request_characteristic: tuple[str, set[str]] | None = None
        self._serial_number: str | None = None
        self._device_list_is_twin_valve: bool | None = None
        self._device_list_decoded_password: ValveDecodedPassword | None = None
        self._authentication_protocol = ValveAuthenticationProtocol.UNKNOWN
        self._device_list_password_state = ValvePasswordDecodeState.UNKNOWN
        self._device_list_password_retries = 0
        self._device_list_authentication_state = ValveAuthenticationState.UNKNOWN
        self._device_list_connection_counter: int | None = None
        self._authentication_failed = False
        self._authentication_failed_passcode: str | None = None
        self._dashboard_data: ValveDashboardData | None = None
        self._dashboard_listeners: list[Callable[[ValveDashboardData | None], None]] = []
        self._authentication_listeners: list[Callable[[bool], None]] = []
        self._passcode_getter = passcode_getter
        self._connection_state_callback = connection_state_callback
        self._crc8 = _ChandlerCrc8()
        self._persistent_connection_enabled = False
        self._persistent_poll_interval = DEFAULT_PERSISTENT_POLL_INTERVAL_SECONDS
        self._persistent_task: asyncio.Task[None] | None = None
        self._pending_persistent_client: BaseBleakClient | None = None
        self._persistent_stop_task: asyncio.Task[None] | None = None
        # Watchdog bookkeeping, all in ``loop.time()`` seconds.
        self._poll_task: asyncio.Task[None] | None = None
        self._poll_started: float | None = None
        self._persistent_progress: float | None = None
        self._persistent_stop_started: float | None = None
        self._last_heal_poll: float | None = None
        self._created_at = self._monotonic()
        # Freshness of the data, in ``loop.time()`` seconds and as a UTC time.
        self._last_dashboard_monotonic: float | None = None
        self._last_dashboard_at: datetime | None = None
        self._data_stale = False
        self._freshness_listeners: list[Callable[[bool], None]] = []
        self._age_listeners: list[Callable[[], None]] = []
        self._age_seen: tuple[str, float] | None = None

    @staticmethod
    def _monotonic() -> float:
        """Return a monotonic clock reading in seconds."""

        return time.monotonic()

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

    @property
    def serial_number(self) -> str | None:
        """Return the serial number parsed from the most recent DeviceList packet."""

        return self._serial_number

    @property
    def device_list_is_twin_valve(self) -> bool | None:
        """Return ``True`` if the most recent DeviceList packet identified a twin valve."""

        return self._device_list_is_twin_valve

    @property
    def dashboard_data(self) -> ValveDashboardData | None:
        """Return the parsed data from the most recent Dashboard response."""

        return self._dashboard_data

    @property
    def authentication_lockout(self) -> bool:
        """Return ``True`` if authentication attempts are currently locked out."""

        return self._authentication_failed

    @property
    def persistent_connection_enabled(self) -> bool:
        """Return ``True`` if persistent connections are currently requested."""

        return self._persistent_connection_enabled

    @property
    def persistent_poll_interval(self) -> float:
        """Return the configured persistent poll interval in seconds."""

        return self._persistent_poll_interval

    def add_authentication_listener(
        self, listener: Callable[[bool], None]
    ) -> CALLBACK_TYPE:
        """Register a callback for authentication lockout updates."""

        self._authentication_listeners.append(listener)

        if self._hass is not None:
            self._hass.loop.call_soon(listener, self._authentication_failed)

        def _remove_listener() -> None:
            with contextlib.suppress(ValueError):
                self._authentication_listeners.remove(listener)

        return _remove_listener

    def set_authentication_lockout(self, locked: bool) -> None:
        """Update the authentication lockout state."""

        if locked:
            passcode = self.get_configured_passcode()
            _LOGGER.debug(
                "Valve %s authentication lockout manually enabled", self._address
            )
            self._set_authentication_failed(True, passcode)
            return

        self.clear_authentication_lockout()

    def clear_authentication_lockout(self) -> None:
        """Allow the next connection attempt to retry authentication."""

        if not self._authentication_failed and self._authentication_failed_passcode is None:
            return

        _LOGGER.debug(
            "Valve %s authentication lockout manually cleared", self._address
        )
        self._set_authentication_failed(False)

    async def async_set_persistent_connection_enabled(self, enabled: bool) -> None:
        """Enable or disable persistent connections for this valve."""

        if enabled:
            if self._persistent_connection_enabled:
                return
            _LOGGER.debug(
                "Persistent connection requested for valve %s", self._address
            )
            self._persistent_connection_enabled = True
            self._update_freshness()
            self._cancel_cooldown()
            self._next_connection_time = None
            self.schedule_poll()
            return

        if not self._persistent_connection_enabled:
            return

        _LOGGER.debug(
            "Persistent connection disabled for valve %s", self._address
        )
        self._persistent_connection_enabled = False
        self._update_freshness()
        await self._async_stop_persistent_session()

    async def async_set_persistent_poll_interval(self, seconds: float) -> None:
        """Update the poll interval used during persistent connections."""

        try:
            value = float(seconds)
        except (TypeError, ValueError):
            value = DEFAULT_PERSISTENT_POLL_INTERVAL_SECONDS

        value = max(
            MIN_PERSISTENT_POLL_INTERVAL_SECONDS,
            min(value, MAX_PERSISTENT_POLL_INTERVAL_SECONDS),
        )

        if self._persistent_poll_interval == value:
            return

        _LOGGER.debug(
            "Persistent poll interval for valve %s updated to %.1f seconds",
            self._address,
            value,
        )
        self._persistent_poll_interval = value
        self._update_freshness()

    def _get_passcode_configuration(self) -> ValvePasscodeConfiguration:
        """Return the stored passcode configuration for this valve."""

        if self._passcode_getter is None:
            return ValvePasscodeConfiguration(value=None, is_override=False)

        return self._passcode_getter(self._address)

    def get_configured_passcode(self) -> str | None:
        """Return the configured passcode for this valve, if available."""

        configuration = self._get_passcode_configuration()
        passcode = configuration.value

        if passcode is None:
            return DEFAULT_VALVE_PASSCODE

        normalized = str(passcode).strip()
        if not normalized:
            return DEFAULT_VALVE_PASSCODE

        return normalized

    def add_dashboard_listener(
        self, listener: Callable[[ValveDashboardData | None], None]
    ) -> CALLBACK_TYPE:
        """Register a callback for Dashboard data updates."""

        self._dashboard_listeners.append(listener)

        if self._dashboard_data is not None:
            self._hass.loop.call_soon(listener, self._dashboard_data)

        def _remove_listener() -> None:
            with contextlib.suppress(ValueError):
                self._dashboard_listeners.remove(listener)

        return _remove_listener

    @property
    def last_data_update(self) -> datetime | None:
        """Return when a complete dashboard was last received, if ever."""

        return self._last_dashboard_at

    @property
    def stale_after_seconds(self) -> float:
        """Return how old dashboard data may get before it is considered stale."""

        if self._persistent_connection_enabled:
            return max(
                STALE_AFTER_PERSISTENT_MIN_SECONDS,
                STALE_AFTER_PERSISTENT_INTERVALS * self._persistent_poll_interval,
            )
        return STALE_AFTER_ON_DEMAND_SECONDS

    @property
    def data_is_stale(self) -> bool:
        """Return ``True`` if no fresh dashboard arrived within the stale limit.

        A valve that has never answered counts from when it was first seen, so
        a valve that never produces data eventually reports stale too.
        """

        reference = (
            self._last_dashboard_monotonic
            if self._last_dashboard_monotonic is not None
            else self._created_at
        )
        return self._monotonic() - reference > self.stale_after_seconds

    def add_freshness_listener(
        self, listener: Callable[[bool], None]
    ) -> CALLBACK_TYPE:
        """Register a callback invoked with ``True`` when data turns stale."""

        self._freshness_listeners.append(listener)

        def _remove_listener() -> None:
            with contextlib.suppress(ValueError):
                self._freshness_listeners.remove(listener)

        return _remove_listener

    @property
    def data_age_label(self) -> str:
        """Describe how old the data is, in coarse steps (see ``DATA_AGE_TIERS``)."""

        if self._last_dashboard_monotonic is None:
            return DATA_AGE_NONE
        age = self._monotonic() - self._last_dashboard_monotonic
        for limit, label in DATA_AGE_TIERS:
            if age < limit:
                return label
        return DATA_AGE_OLDEST

    def add_age_listener(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        """Register a callback for when the data age step or the stale limit changes."""

        self._age_listeners.append(listener)

        def _remove_listener() -> None:
            with contextlib.suppress(ValueError):
                self._age_listeners.remove(listener)

        return _remove_listener

    def _update_freshness(self) -> None:
        """Refresh the stale flag and the coarse data age, notifying on changes."""

        # Age first, so an entity never publishes the old step next to new data.
        self._update_data_age()
        self._update_stale_flag()

    def _update_data_age(self) -> None:
        """Notify age listeners only when the step or the stale limit changed."""

        seen = (self.data_age_label, self.stale_after_seconds)
        if seen == self._age_seen:
            return
        self._age_seen = seen
        for listener in list(self._age_listeners):
            try:
                listener()
            except Exception:  # pragma: no cover - listener failures are logged
                _LOGGER.exception(
                    "Unexpected error in data age listener for valve %s",
                    self._address,
                )

    def _update_stale_flag(self) -> None:
        """Notify listeners if the stale state changed since the last check."""

        stale = self.data_is_stale
        if stale == self._data_stale:
            return
        self._data_stale = stale
        if stale:
            _LOGGER.warning(
                "No fresh data from valve %s for over %.0f seconds; its sensors "
                "are now unavailable",
                self._address,
                self.stale_after_seconds,
            )
        else:
            _LOGGER.info("Fresh data from valve %s is flowing again", self._address)
        for listener in list(self._freshness_listeners):
            try:
                listener(stale)
            except Exception:  # pragma: no cover - listener failures are logged
                _LOGGER.exception(
                    "Unexpected error in freshness listener for valve %s",
                    self._address,
                )

    def watchdog_check(self) -> None:
        """Recover stuck work, refresh the stale flag and nudge idle valves.

        Runs on a timer, independently of polls, so it still works when every
        poll is stuck or failing.
        """

        if self._unloaded:
            return

        self._recover_stuck_work()
        self._update_freshness()

        if not self.available:
            return
        if not (self._data_stale or self._persistent_connection_enabled):
            return
        if self._lock.locked() or self._persistent_task_active():
            return

        now = self._monotonic()
        if (
            self._last_heal_poll is not None
            and now - self._last_heal_poll < HEAL_POLL_MIN_INTERVAL_SECONDS
        ):
            return
        self._last_heal_poll = now
        _LOGGER.debug("Watchdog is polling idle valve %s", self._address)
        self.schedule_poll()

    def update_from_advertisement(self, advertisement: ValveAdvertisement) -> None:
        """Record the most recent Bluetooth advertisement for the valve."""

        self._advertisement = advertisement
        self._available = True
        self._last_seen = dt_util.utcnow()
        if self._authentication_protocol is ValveAuthenticationProtocol.UNKNOWN:
            firmware = advertisement.firmware_version
            if advertisement.is_twin_valve:
                self._set_authentication_protocol(ValveAuthenticationProtocol.COUNTER)
            elif firmware is not None and firmware > 0:
                self._set_authentication_protocol(
                    ValveAuthenticationProtocol.CLASSIC
                    if firmware < 419 else ValveAuthenticationProtocol.COUNTER
                )
            elif advertisement.has_connection_counter is True:
                self._set_authentication_protocol(ValveAuthenticationProtocol.COUNTER)

    def _set_authentication_protocol(self, protocol: ValveAuthenticationProtocol) -> None:
        """Retain a resolved protocol independently of partial advertisements."""

        if protocol is self._authentication_protocol:
            return
        self._authentication_protocol = protocol
        _LOGGER.debug("Valve %s authentication protocol: %s", self._address, protocol.value)

    def _reset_device_list_state(self) -> None:
        """Require fresh access confirmation without forgetting the protocol."""

        self._device_list_authentication_state = ValveAuthenticationState.UNKNOWN
        self._device_list_connection_counter = None
        self._device_list_decoded_password = None
        self._device_list_password_state = ValvePasswordDecodeState.UNKNOWN

    def _has_dashboard_access(self) -> bool:
        """Allow access only after authentication or confirmed unprotected access."""

        if self._device_list_authentication_state == ValveAuthenticationState.AUTHENTICATED:
            return True
        decoded = self._device_list_decoded_password
        return (
            self._advertisement is not None
            and self._advertisement.authentication_required is False
            and self._authentication_protocol is ValveAuthenticationProtocol.CLASSIC
            and decoded is not None
            and decoded.state in (
                ValvePasswordDecodeState.VALID,
                ValvePasswordDecodeState.RECOVERED,
            )
        )

    def mark_unavailable(self) -> None:
        """Mark the valve as temporarily unavailable."""

        self._available = False

    def schedule_poll(self) -> None:
        """Schedule a background poll of the valve."""

        if self._hass.state != CoreState.running:
            _LOGGER.debug(
                "Skipping poll for valve %s; Home Assistant not fully started",
                self._address,
            )
            return

        if not self.available:
            return
        self._hass.async_create_task(self.async_poll())

    def _cancel_cooldown(self) -> None:
        """Cancel any scheduled retry callback."""

        if self._cooldown_cancel is not None:
            self._cooldown_cancel()
            self._cooldown_cancel = None

    def _set_connection_cooldown(self) -> None:
        """Record the time when the next connection attempt is allowed."""

        self._next_connection_time = dt_util.utcnow() + CONNECTION_MIN_RETRY_INTERVAL

    def _schedule_cooldown_retry(self, delay: float) -> None:
        """Schedule a poll retry once the cooldown expires."""

        if self._cooldown_cancel is not None:
            return

        if delay <= 0:
            self._handle_cooldown_complete(dt_util.utcnow())
            return

        self._cooldown_cancel = async_call_later(
            self._hass, delay, self._handle_cooldown_complete
        )

    @callback
    def _handle_cooldown_complete(self, _: datetime) -> None:
        """Retry polling after the cooldown period."""

        self._cooldown_cancel = None
        if self.available:
            self.schedule_poll()

    def _persistent_task_active(self) -> bool:
        """Return ``True`` while a persistent session or its cleanup is active."""

        if self._persistent_stop_task is not None:
            return True
        task = self._persistent_task
        if task is None:
            return False
        if task.done():
            with contextlib.suppress(Exception):
                task.result()
            self._persistent_task = None
            return False
        return True

    async def _async_stop_persistent_session(self) -> None:
        """Cancel the persistent polling session if one is active."""

        stop_task = self._persistent_stop_task
        if stop_task is None:
            task = self._persistent_task
            if task is None:
                return

            # Claim the client before cancellation; an unstarted task cannot
            # enter its finally block. Other stop callers must join this worker.
            pending_client = self._pending_persistent_client
            self._pending_persistent_client = None
            task.cancel()
            stop_task = asyncio.create_task(
                self._async_finish_persistent_stop(task, pending_client)
            )
            self._persistent_stop_task = stop_task
            self._persistent_stop_started = self._monotonic()
        try:
            # Bounded so a stalled disconnect cannot block a switch-off, an
            # integration reload or Home Assistant shutdown.
            await _async_wait_for_cleanup(stop_task, UNLOAD_TIMEOUT_SECONDS)
        finally:
            if self._persistent_stop_task is stop_task:
                self._persistent_stop_task = None
                self._persistent_stop_started = None
                if self._persistent_connection_enabled and not self._unloaded:
                    # Re-enabling during teardown may have scheduled a poll
                    # that was skipped while this stop worker still owned cleanup.
                    self.schedule_poll()

    async def _async_finish_persistent_stop(
        self,
        task: asyncio.Task[None],
        pending_client: BaseBleakClient | None,
    ) -> None:
        """Join the cancelled session and disconnect any unclaimed client."""

        try:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        finally:
            try:
                if pending_client is not None:
                    await self._async_disconnect_client(pending_client)
            finally:
                if self._persistent_task is task:
                    self._persistent_task = None

    def _can_start_persistent_session(self) -> bool:
        """Return ``True`` if a persistent session may be started."""

        if not self._persistent_connection_enabled or self._unloaded:
            return False

        if self._persistent_task_active():
            return False

        return self._has_dashboard_access()

    def _try_begin_persistent_session(self, client: BaseBleakClient) -> bool:
        """Start a persistent polling session if conditions allow."""

        if not self._can_start_persistent_session():
            return False

        _LOGGER.debug(
            "Maintaining persistent connection to valve %s", self._address
        )
        self._pending_persistent_client = client
        keepalive = self._async_persistent_keepalive_loop(client)
        try:
            self._persistent_task = self._hass.loop.create_task(keepalive)
        except Exception:
            self._pending_persistent_client = None
            keepalive.close()
            raise
        return True

    async def _async_persistent_keepalive_loop(
        self, client: BaseBleakClient
    ) -> None:
        """Keep the BLE connection alive and poll on a frequent schedule."""

        try:
            # From this point, this coroutine's finally block owns cleanup.
            self._pending_persistent_client = None
            self._persistent_progress = self._monotonic()
            while True:
                if (
                    not self._persistent_connection_enabled
                    or self._unloaded
                    or not getattr(client, "is_connected", False)
                ):
                    break

                interval = max(
                    MIN_PERSISTENT_POLL_INTERVAL_SECONDS,
                    min(
                        self._persistent_poll_interval,
                        MAX_PERSISTENT_POLL_INTERVAL_SECONDS,
                    ),
                )

                try:
                    await asyncio.sleep(interval)
                except asyncio.CancelledError:
                    raise

                if (
                    not self._persistent_connection_enabled
                    or self._unloaded
                    or not getattr(client, "is_connected", False)
                ):
                    break

                self._persistent_progress = self._monotonic()
                try:
                    async with asyncio.timeout(PERSISTENT_REQUEST_TIMEOUT_SECONDS):
                        request_sent, response_received = (
                            await self._async_request_dashboard(client)
                        )
                except asyncio.CancelledError:
                    raise
                except TimeoutError:
                    _LOGGER.warning(
                        "Persistent poll of valve %s timed out after %g seconds; "
                        "reconnecting",
                        self._address,
                        PERSISTENT_REQUEST_TIMEOUT_SECONDS,
                    )
                    break
                except Exception:  # pragma: no cover - unexpected protocol errors
                    _LOGGER.exception(
                        "Unexpected error while refreshing dashboard data for valve %s",
                        self._address,
                    )
                    break

                if not request_sent:
                    _LOGGER.debug(
                        "Stopping persistent polling for valve %s; unable to send Dashboard request",
                        self._address,
                    )
                    break

                if response_received:
                    self._last_success = dt_util.utcnow()
                    self._persistent_progress = self._monotonic()
                else:
                    _LOGGER.debug(
                        "Valve %s did not provide a Dashboard response during persistent polling",
                        self._address,
                    )
                    break
        except asyncio.CancelledError:
            _LOGGER.debug(
                "Persistent polling task for valve %s was cancelled", self._address
            )
            raise
        finally:
            try:
                await self._async_disconnect_client(client)
            finally:
                if self._persistent_task is asyncio.current_task():
                    self._persistent_task = None
                    self._persistent_progress = None

                if (
                    self._persistent_connection_enabled
                    and not self._unloaded
                ):
                    # The link just dropped; reconnect soon instead of waiting
                    # out the full on-demand retry interval.
                    self._next_connection_time = dt_util.utcnow() + min(
                        CONNECTION_MIN_RETRY_INTERVAL,
                        timedelta(seconds=PERSISTENT_RECONNECT_DELAY_SECONDS),
                    )
                    self.schedule_poll()

    async def async_unload(self) -> None:
        """Prevent future polls and wait for any active poll to finish."""

        self._unloaded = True
        self._persistent_connection_enabled = False
        self._cancel_cooldown()
        await _async_wait_for_cleanup(
            asyncio.create_task(self._async_finish_unload()),
            UNLOAD_TIMEOUT_SECONDS * 3,
        )

    async def _async_finish_unload(self) -> None:
        """Wait for connection cleanup, including an active one-shot poll.

        A poll that is still running gets a grace period to finish and
        disconnect. Nothing here waits unboundedly: after the grace period the
        poll is cancelled and abandoned, even if it is stuck in a Bluetooth
        call that ignores cancellation, which is what lets an integration
        reload recover from a hung poll.
        """

        await self._async_stop_persistent_session()
        try:
            async with asyncio.timeout(UNLOAD_TIMEOUT_SECONDS):
                async with self._lock:
                    return
        except TimeoutError:
            _LOGGER.warning(
                "Abandoning a stuck poll of valve %s during unload", self._address
            )
            poll_task = self._poll_task
            if poll_task is not None and not poll_task.done():
                poll_task.cancel()

    async def async_poll(self) -> None:
        """Attempt to connect to the valve and fetch additional data."""

        if not self.available:
            return

        self._recover_stuck_work()

        if self._persistent_task_active():
            _LOGGER.debug(
                "Skipping poll for %s; persistent session or cleanup is still active",
                self._address,
            )
            return

        if self._lock.locked():
            _LOGGER.debug(
                "Skipping poll for %s; another poll is already running", self._address
            )
            return

        lock = self._lock
        async with lock:
            self._poll_task = asyncio.current_task()
            self._poll_started = self._monotonic()
            try:
                await self._async_poll_locked()
            finally:
                # An abandoned poll must not clear its replacement's bookkeeping.
                if self._lock is lock:
                    self._poll_task = None
                    self._poll_started = None

    def _recover_stuck_work(self) -> None:
        """Abandon a poll or persistent session that outlived its hard limit.

        Every Bluetooth call is bounded, so this only fires if a call ignored
        both its timeout and cancellation. Cancelling and replacing the lock
        lets polling resume even though the stuck task can no longer be joined.
        """

        now = self._monotonic()

        if (
            self._poll_started is not None
            and now - self._poll_started > POLL_HARD_LIMIT_SECONDS
        ):
            _LOGGER.warning(
                "Poll of valve %s has been running for %.0f seconds; abandoning it",
                self._address,
                now - self._poll_started,
            )
            task = self._poll_task
            if task is not None and not task.done():
                task.cancel()
            self._poll_task = None
            self._poll_started = None
            self._lock = asyncio.Lock()

        task = self._persistent_task
        if (
            task is not None
            and not task.done()
            and self._persistent_progress is not None
            and now - self._persistent_progress
            > POLL_HARD_LIMIT_SECONDS + self._persistent_poll_interval
        ):
            _LOGGER.warning(
                "Persistent session for valve %s made no progress for %.0f seconds; "
                "abandoning it",
                self._address,
                now - self._persistent_progress,
            )
            task.cancel()
            self._persistent_task = None
            self._pending_persistent_client = None
            self._persistent_progress = None

        stop_task = self._persistent_stop_task
        if (
            stop_task is not None
            and not stop_task.done()
            and self._persistent_stop_started is not None
            and now - self._persistent_stop_started > POLL_HARD_LIMIT_SECONDS
        ):
            _LOGGER.warning(
                "Persistent session cleanup for valve %s is stuck; abandoning it",
                self._address,
            )
            stop_task.cancel()
            self._persistent_stop_task = None
            self._persistent_stop_started = None

    async def _async_poll_locked(self) -> None:
        """Perform a Bluetooth connection cycle for the valve."""

        now = dt_util.utcnow()
        next_connection_time = self._next_connection_time
        if next_connection_time is not None and now < next_connection_time:
            remaining = max((next_connection_time - now).total_seconds(), 0)
            _LOGGER.debug(
                "Skipping poll for %s; retrying after %.1f seconds",
                self._address,
                remaining,
            )
            self._schedule_cooldown_retry(remaining)
            return

        connection_attempted = False
        cleanup_client: BaseBleakClient | None = None

        try:
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

            _LOGGER.debug(
                "Connecting to valve %s to refresh diagnostic data", self._address
            )

            connection_attempted = True
            self._cancel_cooldown()
            self._reset_device_list_state()

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

            cleanup_client = client

            try:
                if self._connection_state_callback is not None:
                    self._connection_state_callback(self._address, True)
                async with asyncio.timeout(POLL_FETCH_TIMEOUT_SECONDS):
                    refreshed = await self._async_fetch_device_information(client)
            except TimeoutError:
                _LOGGER.warning(
                    "Timed out exchanging data with valve %s after %g seconds; "
                    "disconnecting",
                    self._address,
                    POLL_FETCH_TIMEOUT_SECONDS,
                )
            except Exception:  # pragma: no cover - future protocol work may raise
                _LOGGER.exception(
                    "Error while retrieving extended data from valve %s", self._address
                )
            else:
                if refreshed:
                    self._last_success = dt_util.utcnow()
                if refreshed and self._try_begin_persistent_session(client):
                    cleanup_client = None
            finally:
                if cleanup_client is not None:
                    await self._async_disconnect_client(cleanup_client)
        finally:
            if connection_attempted:
                self._set_connection_cooldown()

    async def _async_fetch_device_information(
        self, client: BaseBleakClient
    ) -> bool:
        """Return whether a fresh dashboard was retrieved from the valve."""

        advertisement = self._advertisement
        if advertisement is None:
            return False

        model = advertisement.model
        manufacturer_data_complete = advertisement.manufacturer_data_complete

        if model not in (None, "Evb019"):
            _LOGGER.debug(
                "Connected to valve %s (%s); requests are only defined for Evb019 valves",
                self._address,
                model or "unknown model",
            )
            return False

        if model is None and not manufacturer_data_complete:
            _LOGGER.debug(
                "Valve %s advertisement was incomplete; attempting DeviceList probe",
                self._address,
            )

        request_sent, response_received = await self._async_request_device_list(client)
        if not request_sent:
            _LOGGER.debug(
                "Unable to send DeviceList request to valve %s; will retry on next poll",
                self._address,
            )
            return False

        if response_received:
            _LOGGER.debug(
                "Retrieved DeviceList response from valve %s during diagnostic poll",
                self._address,
            )
        else:
            _LOGGER.debug(
                "Valve %s did not provide a DeviceList response during this poll",
                self._address,
            )

        if not response_received or not self._has_dashboard_access():
            passcode = self.get_configured_passcode()
            if passcode is not None:
                passcode = passcode.strip()
            self._reset_authentication_failure_if_needed(passcode)

            if passcode is None:
                _LOGGER.debug(
                    "Skipping Dashboard request to valve %s; authentication is required and no passcode is configured",
                    self._address,
                )
                return False

            if self._authentication_failed and (
                passcode == self._authentication_failed_passcode
            ):
                _LOGGER.debug(
                    "Skipping Dashboard request to valve %s; authentication was previously attempted and failed",
                    self._address,
                )
                return False

            if self._parse_passcode(passcode) is None:
                _LOGGER.debug(
                    "Skipping Dashboard request to valve %s; configured passcode is not numeric",
                    self._address,
                )
                return False

            if self._device_list_password_state == ValvePasswordDecodeState.AUTH_NEEDED:
                _LOGGER.debug(
                    "Skipping Dashboard request to valve %s; valve still reports that authentication is required",
                    self._address,
                )
                return False

            _LOGGER.debug(
                "Skipping Dashboard request to valve %s; authentication has not been confirmed",
                self._address,
            )
            return False

        dashboard_request_sent, dashboard_response_received = (
            await self._async_request_dashboard(client)
        )
        if not dashboard_request_sent:
            _LOGGER.debug(
                "Unable to send Dashboard request to valve %s; will retry on next poll",
                self._address,
            )
            return False

        if dashboard_response_received:
            _LOGGER.debug(
                "Retrieved Dashboard response from valve %s during diagnostic poll",
                self._address,
            )
        else:
            _LOGGER.debug(
                "Valve %s did not provide a Dashboard response during this poll",
                self._address,
            )
        return dashboard_response_received

    @staticmethod
    def _create_request_payload(request: ValveRequestCommand | int) -> bytes:
        """Return the 20-byte EVB019 payload for the provided request value."""

        value = int(request)
        if not 0 <= value <= 255:
            raise ValueError(f"Invalid request value {value}; must be 0-255")
        return bytes([value] * _EVB019_REQUEST_PACKET_LENGTH)

    async def _async_resolve_request_characteristic(
        self, client: BaseBleakClient, characteristic_uuid: str | None = None
    ) -> tuple[str, set[str]] | None:
        """Return the writable GATT characteristic used for EVB019 requests."""

        if characteristic_uuid is None and self._request_characteristic is not None:
            return self._request_characteristic

        try:
            services = await self._async_get_services(client)
        except Exception as exc:  # pragma: no cover - bleak raises platform errors
            _LOGGER.debug(
                "Unable to resolve GATT services for valve %s: %s",
                self._address,
                exc,
            )
            return None

        if not services:
            _LOGGER.debug(
                "Valve %s did not provide any GATT services during discovery",
                self._address,
            )
            return None

        if characteristic_uuid is not None:
            candidate = self._locate_characteristic(
                services,
                characteristic_uuid=characteristic_uuid,
            )
            if candidate is None:
                _LOGGER.debug(
                    "Valve %s does not expose writable characteristic %s",
                    self._address,
                    characteristic_uuid,
                )
                return None

            uuid, properties, characteristic = candidate
            if not properties.intersection({"write", "write_without_response"}):
                _LOGGER.debug(
                    "Characteristic %s on valve %s does not support writes",
                    characteristic_uuid,
                    self._address,
                )
                return None

            if self._characteristic_cannot_write_without_response(
                characteristic, properties
            ):
                _LOGGER.debug(
                    "Characteristic %s on valve %s cannot accept EVB019 request payloads",
                    characteristic_uuid,
                    self._address,
                )
                return None

            return (uuid, properties)

        attempted: set[str] = set()
        for profile in _EVB019_GATT_PROFILES:
            candidate = self._locate_characteristic(
                services,
                characteristic_uuid=profile.write_char_uuid,
                service_uuid=profile.service_uuid,
                required_properties={"write", "write_without_response"},
            )
            if candidate is None:
                continue

            uuid, properties, characteristic = candidate
            attempted.add(uuid.lower())
            if self._characteristic_cannot_write_without_response(
                characteristic, properties
            ):
                continue

            resolved = (uuid, properties)
            self._request_characteristic = resolved
            return resolved

        for _, characteristic in self._iter_gatt_characteristics(services):
            uuid = getattr(characteristic, "uuid", None)
            if not isinstance(uuid, str):
                continue

            if uuid.lower() in attempted:
                continue

            properties = set(getattr(characteristic, "properties", ()) or ())
            if not properties.intersection({"write", "write_without_response"}):
                continue

            if self._characteristic_cannot_write_without_response(
                characteristic, properties
            ):
                continue

            resolved = (uuid, properties)
            self._request_characteristic = resolved
            return resolved

        if characteristic_uuid is None:
            _LOGGER.debug(
                "Valve %s does not expose a writable characteristic suitable for EVB019 requests",
                self._address,
            )
        else:
            _LOGGER.debug(
                "Valve %s does not expose writable characteristic %s",
                self._address,
                characteristic_uuid,
            )
        return None

    async def _async_send_payload(
        self,
        client: BaseBleakClient,
        payload: bytes,
        *,
        command_name: str,
        characteristic_uuid: str | None = None,
        response: bool | None = None,
    ) -> bool:
        """Send a raw EVB019 payload to the connected valve."""

        resolved = await self._async_resolve_request_characteristic(
            client, characteristic_uuid
        )
        if resolved is None:
            _LOGGER.debug(
                "Cannot send %s to valve %s; request characteristic not found",
                command_name,
                self._address,
            )
            return False

        char_uuid, properties = resolved

        if response is None:
            write_with_response = "write" in properties
        elif response and "write" not in properties:
            write_with_response = False
        else:
            write_with_response = response

        try:
            await _async_bounded(
                client.write_gatt_char(
                    char_uuid, payload, response=write_with_response
                ),
                BLE_OPERATION_TIMEOUT_SECONDS,
                f"{command_name} write to valve {self._address}",
            )
        except TimeoutError:
            return False
        except BLEAK_RETRY_EXCEPTIONS as exc:
            _LOGGER.debug(
                "Failed to send %s to valve %s via %s: %s",
                command_name,
                self._address,
                char_uuid,
                exc,
            )
            return False
        except Exception:  # pragma: no cover - unexpected Bluetooth errors are logged
            _LOGGER.exception(
                "Unexpected error while sending %s to valve %s",
                command_name,
                self._address,
            )
            return False

        return True

    async def _async_send_request(
        self,
        client: BaseBleakClient,
        request: ValveRequestCommand | int,
        *,
        characteristic_uuid: str | None = None,
        response: bool | None = None,
    ) -> bool:
        """Send an EVB019 request packet to the connected valve."""

        command_value = int(request)
        payload = self._create_request_payload(command_value)

        try:
            command_name = (
                ValveRequestCommand(command_value).name.title().replace("_", "")
            )
        except ValueError:
            command_name = f"value {command_value}"

        return await self._async_send_payload(
            client,
            payload,
            command_name=f"{command_name} request",
            characteristic_uuid=characteristic_uuid,
            response=response,
        )

    async def _async_send_reset_buffer_packet(self, client: BaseBleakClient) -> bool:
        """Send the EVB019 reset buffer packet before disconnecting.

        Returns True if the reset packet was successfully sent.
        """

        advertisement = self._advertisement
        if advertisement is None or advertisement.model != "Evb019":
            return False

        sent = await self._async_send_request(
            client,
            ValveRequestCommand.RESET,
        )
        if sent:
            _LOGGER.debug(
                "Sent reset buffer request to valve %s prior to disconnect",
                self._address,
            )
        else:
            _LOGGER.debug(
                "Unable to send reset buffer request to valve %s prior to disconnect",
                self._address,
            )

        return sent

    async def _async_disconnect_client(self, client: BaseBleakClient) -> None:
        """Drain bounded reset and disconnect attempts before releasing the client."""

        async def _cleanup() -> None:
            try:
                try:
                    async with asyncio.timeout(_RESET_BUFFER_TIMEOUT_SECONDS):
                        if await self._async_send_reset_buffer_packet(client):
                            await asyncio.sleep(0.1)
                except TimeoutError:
                    _LOGGER.warning(
                        "Timed out resetting valve %s before disconnect", self._address
                    )
                except Exception:
                    _LOGGER.debug(
                        "Error resetting valve %s before disconnect",
                        self._address,
                        exc_info=True,
                    )
            finally:
                # RESET is best effort and must not use the disconnect budget.
                try:
                    async with asyncio.timeout(_DISCONNECT_TIMEOUT_SECONDS):
                        await client.disconnect()
                except TimeoutError:
                    _LOGGER.warning(
                        "Timed out disconnecting from valve %s; the Bluetooth "
                        "connection may still be open",
                        self._address,
                    )
                except Exception:
                    _LOGGER.warning(
                        "Error disconnecting from valve %s; the Bluetooth "
                        "connection may still be open",
                        self._address,
                        exc_info=True,
                    )

        try:
            await _async_wait_for_cleanup(asyncio.create_task(_cleanup()))
        finally:
            # Session availability must end even if cleanup failed or its
            # caller was cancelled. A silent valve may have a deferred loss.
            if self._connection_state_callback is not None:
                self._connection_state_callback(self._address, False)

    async def _async_request_device_list(
        self, client: BaseBleakClient
    ) -> tuple[bool, bool]:
        """Send a DeviceList request and wait for a matching response packet."""

        self._reset_device_list_state()
        loop = asyncio.get_running_loop()
        response_future: asyncio.Future[bytes] | None = loop.create_future()

        def _notification_handler(_: int | str, data: bytearray) -> None:
            nonlocal response_future

            future = response_future
            if future is None or future.done():
                return

            packet = bytes(data)
            if self._is_device_list_packet(packet):
                future.set_result(packet)

        subscriptions = await self._async_subscribe_to_notifications(
            client, _notification_handler
        )

        try:
            request_sent = await self._async_send_request(
                client, ValveRequestCommand.DEVICE_LIST
            )
            if not request_sent:
                if response_future is not None and not response_future.done():
                    response_future.cancel()
                return False, False

            if not subscriptions:
                if response_future is not None and not response_future.done():
                    response_future.cancel()
                _LOGGER.debug(
                    "Valve %s does not expose a notifying characteristic for DeviceList responses",
                    self._address,
                )
                return True, False

            try:
                async with asyncio.timeout(
                    _DEVICE_LIST_RESPONSE_TIMEOUT_SECONDS
                ):
                    assert response_future is not None
                    packet = await response_future
            except asyncio.TimeoutError:
                if response_future is not None and not response_future.done():
                    response_future.cancel()
                _LOGGER.debug(
                    "Timed out waiting for DeviceList response from valve %s",
                    self._address,
                )
                return True, False
            except asyncio.CancelledError:
                raise
            except Exception:
                if response_future is not None and not response_future.done():
                    response_future.cancel()
                _LOGGER.exception(
                    "Unexpected error while waiting for DeviceList response from valve %s",
                    self._address,
                )
                return True, False

            response_future = None
            self._handle_device_list_packet(packet)
            response_received = True

            passcode = self.get_configured_passcode()
            if passcode is not None:
                passcode = passcode.strip()

            self._reset_authentication_failure_if_needed(passcode)

            passcode_value = self._parse_passcode(passcode)
            if passcode is not None and passcode_value is None:
                _LOGGER.debug(
                    "Skipping authentication for valve %s; configured passcode is not numeric",
                    self._address,
                )

            if self._should_attempt_authentication(passcode, passcode_value):
                connection_counter = self._device_list_connection_counter
                if connection_counter is None:
                    _LOGGER.debug(
                        "Skipping authentication for valve %s; DeviceList response did not include a connection counter",
                        self._address,
                    )
                else:
                    authenticated = False
                    sent_attempts = 0
                    for attempt in range(1, _MAX_AUTHENTICATION_ATTEMPTS + 1):
                        response_future = loop.create_future()
                        sent, authenticated = await self._async_authenticate(
                            client,
                            connection_counter,
                            passcode_value,
                            response_future,
                        )
                        response_future = None
                        if not sent:
                            break
                        sent_attempts = attempt
                        if authenticated:
                            break
                        if (
                            self._device_list_authentication_state
                            != ValveAuthenticationState.NOT_AUTHENTICATED
                        ):
                            # A missing or unrecognized reply is not a rejected PIN.
                            break
                        if attempt < _MAX_AUTHENTICATION_ATTEMPTS:
                            _LOGGER.debug(
                                "Valve %s authentication attempt %d/%d failed; retrying",
                                self._address,
                                attempt,
                                _MAX_AUTHENTICATION_ATTEMPTS,
                            )
                            next_counter = self._device_list_connection_counter
                            if next_counter is not None:
                                connection_counter = next_counter

                    if (
                        not authenticated
                        and sent_attempts >= _MAX_AUTHENTICATION_ATTEMPTS
                        and self._device_list_authentication_state
                        == ValveAuthenticationState.NOT_AUTHENTICATED
                    ):
                        self._record_authentication_failure(passcode)

            return True, response_received
        finally:
            if response_future is not None and not response_future.done():
                response_future.cancel()
            await self._async_unsubscribe_notifications(client, subscriptions)

    def _set_authentication_failed(
        self, failed: bool, passcode: str | None = None
    ) -> None:
        """Update the stored authentication failure state."""

        if failed:
            changed = not self._authentication_failed or (
                self._authentication_failed_passcode != passcode
            )
            self._authentication_failed = True
            self._authentication_failed_passcode = passcode
        else:
            changed = self._authentication_failed or (
                self._authentication_failed_passcode is not None
            )
            self._authentication_failed = False
            self._authentication_failed_passcode = None

        if changed:
            self._notify_authentication_listeners()

    def _reset_authentication_failure_if_needed(self, passcode: str | None) -> None:
        """Clear stored failures if the configured passcode has changed."""

        if not self._authentication_failed:
            return

        if passcode is None:
            return

        if self._authentication_failed_passcode is None:
            return

        if passcode == self._authentication_failed_passcode:
            return

        _LOGGER.debug(
            "Configured passcode for valve %s changed; resetting authentication failure state",
            self._address,
        )
        self._set_authentication_failed(False)

    def _record_authentication_failure(self, passcode: str | None) -> None:
        """Record that authentication failed for the provided passcode."""

        self._set_authentication_failed(True, passcode)
        _LOGGER.debug(
            "Authentication attempt for valve %s failed; future attempts will be skipped until Home Assistant restarts or the passcode changes",
            self._address,
        )

    def _should_attempt_authentication(
        self, passcode: str | None, passcode_value: int | None
    ) -> bool:
        """Return ``True`` if authentication should be attempted."""

        if (
            self._authentication_protocol is not ValveAuthenticationProtocol.COUNTER
            or self._device_list_authentication_state
            != ValveAuthenticationState.NOT_AUTHENTICATED
        ):
            return False

        if passcode is None:
            _LOGGER.debug(
                "Skipping authentication for valve %s; authentication is required and no passcode is configured",
                self._address,
            )
            return False

        if self._device_list_authentication_state == ValveAuthenticationState.AUTHENTICATED:
            return False

        if passcode_value is None:
            return False

        if self._authentication_failed and (
            passcode == self._authentication_failed_passcode
        ):
            _LOGGER.debug(
                "Skipping authentication for valve %s; previous attempt failed for the configured passcode",
                self._address,
            )
            return False

        return True

    async def _async_authenticate(
        self,
        client: BaseBleakClient,
        connection_counter: int,
        passcode_value: int,
        response_future: asyncio.Future[bytes],
    ) -> tuple[bool, bool]:
        """Send the authentication payload and wait for a DeviceList response."""

        self._reset_device_list_state()
        payload = self._create_password_buffer(connection_counter, passcode_value)
        _LOGGER.debug(
            "Attempting authentication with valve %s using connection counter %s",
            self._address,
            connection_counter,
        )

        sent = await self._async_send_payload(
            client,
            payload,
            command_name="DeviceList authentication packet",
        )
        if not sent:
            if not response_future.done():
                response_future.cancel()
            return False, False

        try:
            async with asyncio.timeout(_DEVICE_LIST_RESPONSE_TIMEOUT_SECONDS):
                packet = await response_future
        except asyncio.TimeoutError:
            if not response_future.done():
                response_future.cancel()
            _LOGGER.debug(
                "Timed out waiting for authentication response from valve %s",
                self._address,
            )
            return True, False
        except asyncio.CancelledError:
            raise
        except Exception:
            if not response_future.done():
                response_future.cancel()
            _LOGGER.exception(
                "Unexpected error while waiting for authentication response from valve %s",
                self._address,
            )
            return True, False

        self._handle_device_list_packet(packet)

        authenticated = (
            self._device_list_authentication_state
            == ValveAuthenticationState.AUTHENTICATED
        )
        if authenticated:
            _LOGGER.debug("Valve %s authentication succeeded", self._address)
            return True, True

        _LOGGER.debug(
            "Valve %s authentication response did not confirm access; state=%s auth_state=%s",
            self._address,
            self._device_list_password_state.name,
            self._device_list_authentication_state.name,
        )
        return True, False

    def _parse_passcode(self, passcode: str | None) -> int | None:
        """Return the integer value for a configured passcode string."""

        if passcode is None:
            return None

        normalized = passcode.strip()
        if not normalized.isdigit():
            return None

        try:
            return max(0, min(9999, int(normalized)))
        except ValueError:
            return None

    def _create_password_buffer(self, connection_counter: int, passcode: int) -> bytes:
        """Return the authentication payload for the provided parameters."""

        counter = connection_counter & 0xFF
        polynomial = _CRC_RANDOM.choice(_CRC_ALLOWED_POLYNOMIALS)
        buffer = bytearray(self._create_request_payload(ValveRequestCommand.DEVICE_LIST))
        digits = self._get_password_digits(passcode)
        random_seed = _CRC_RANDOM.randint(1, 255)
        self._crc8.set_options(polynomial, random_seed)
        random_xor = _CRC_RANDOM.randint(1, 255) ^ random_seed
        intermediate = counter ^ self._crc8.compute_legacy(random_xor)

        buffer[2] = 80
        buffer[3] = 65
        buffer[4] = polynomial & 0xFF
        buffer[5] = random_seed & 0xFF
        buffer[6] = random_xor & 0xFF
        buffer[7] = (self._crc8.compute_legacy(intermediate) ^ digits[3]) & 0xFF
        buffer[8] = (digits[2] ^ self._crc8.compute_legacy(buffer[7])) & 0xFF
        buffer[9] = (digits[1] ^ self._crc8.compute_legacy(buffer[8])) & 0xFF
        buffer[10] = (digits[0] ^ self._crc8.compute_legacy(buffer[9])) & 0xFF

        for index in range(11, _EVB019_REQUEST_PACKET_LENGTH):
            buffer[index] = _CRC_RANDOM.randint(1, 255)

        return bytes(buffer)

    @staticmethod
    def _get_password_digits(passcode: int) -> tuple[int, int, int, int]:
        """Return the individual digits for a four digit passcode."""

        constrained = max(0, min(9999, passcode))
        thousands, remainder = divmod(constrained, 1000)
        hundreds, remainder = divmod(remainder, 100)
        tens, ones = divmod(remainder, 10)
        return ones, tens, hundreds, thousands

    async def _async_request_dashboard(
        self, client: BaseBleakClient
    ) -> tuple[bool, bool]:
        """Send a Dashboard request and wait for the full multi-packet response."""

        loop = asyncio.get_running_loop()
        response_future: asyncio.Future[list[bytes]] = loop.create_future()
        packets: dict[int, bytes] = {}

        def _notification_handler(_: int | str, data: bytearray) -> None:
            if response_future.done():
                return

            packet = bytes(data)
            index = self._get_dashboard_packet_index(packet, packets)
            status: str
            if index is None:
                status = "ignored"
            else:
                status = f"index {index}"
            _LOGGER.debug(
                "Valve %s Dashboard packet %s -> %s",
                self._address,
                packet.hex(),
                status,
            )
            if index is None:
                return

            packets[index] = packet
            if len(packets) == _DASHBOARD_PACKET_COUNT:
                try:
                    ordered = [packets[i] for i in range(_DASHBOARD_PACKET_COUNT)]
                except KeyError:
                    return
                response_future.set_result(ordered)

        subscriptions = await self._async_subscribe_to_notifications(
            client, _notification_handler
        )

        try:
            request_sent = await self._async_send_request(
                client, ValveRequestCommand.DASHBOARD
            )
            if not request_sent:
                if not response_future.done():
                    response_future.cancel()
                return False, False

            if not subscriptions:
                if not response_future.done():
                    response_future.cancel()
                _LOGGER.debug(
                    "Valve %s does not expose a notifying characteristic for Dashboard responses",
                    self._address,
                )
                return True, False

            try:
                async with asyncio.timeout(
                    _DASHBOARD_RESPONSE_TIMEOUT_SECONDS
                ):
                    packets_list = await response_future
            except asyncio.TimeoutError:
                if not response_future.done():
                    response_future.cancel()
                _LOGGER.debug(
                    "Timed out waiting for Dashboard response from valve %s",
                    self._address,
                )
                return True, False
            except asyncio.CancelledError:
                raise
            except Exception:
                if not response_future.done():
                    response_future.cancel()
                _LOGGER.exception(
                    "Unexpected error while waiting for Dashboard response from valve %s",
                    self._address,
                )
                return True, False

            self._handle_dashboard_packets(packets_list)
            return True, True
        finally:
            await self._async_unsubscribe_notifications(client, subscriptions)

    async def _async_subscribe_to_notifications(
        self,
        client: BaseBleakClient,
        handler: Callable[[int | str, bytearray], None],
    ) -> list[str]:
        """Subscribe to every notifying characteristic exposed by the valve."""

        try:
            services = await self._async_get_services(client)
        except Exception as exc:  # pragma: no cover - bleak raises platform errors
            _LOGGER.debug(
                "Unable to resolve GATT services for valve %s while preparing notifications: %s",
                self._address,
                exc,
            )
            return []

        subscriptions: list[str] = []
        subscribed: set[str] = set()
        attempted: set[str] = set()

        for profile in _EVB019_GATT_PROFILES:
            candidate = self._locate_characteristic(
                services,
                characteristic_uuid=profile.notify_char_uuid,
                service_uuid=profile.service_uuid,
            )
            if candidate is None:
                continue

            uuid, properties, _ = candidate
            normalized = uuid.lower()
            attempted.add(normalized)
            if not properties.intersection({"notify", "indicate"}):
                continue

            if await self._async_try_start_notify(client, uuid, handler):
                subscriptions.append(uuid)
                subscribed.add(normalized)

        for _, characteristic in self._iter_gatt_characteristics(services):
            uuid = getattr(characteristic, "uuid", None)
            if not isinstance(uuid, str):
                continue

            normalized = uuid.lower()
            if normalized in attempted or normalized in subscribed:
                continue

            properties = set(getattr(characteristic, "properties", ()) or ())
            if not properties.intersection({"notify", "indicate"}):
                continue

            if await self._async_try_start_notify(client, uuid, handler):
                subscriptions.append(uuid)
                subscribed.add(normalized)

        return subscriptions

    async def _async_unsubscribe_notifications(
        self, client: BaseBleakClient, subscriptions: Iterable[str]
    ) -> None:
        """Cancel notification subscriptions for the provided characteristic UUIDs."""

        for uuid in subscriptions:
            with contextlib.suppress(Exception):
                await _async_bounded(
                    client.stop_notify(uuid),
                    BLE_OPERATION_TIMEOUT_SECONDS,
                    f"notification unsubscribe on valve {self._address}",
                )

    async def _async_get_services(self, client: BaseBleakClient):
        """Return the GATT services exposed by the connected client."""

        services_property = getattr(client.__class__, "services", None)
        if isinstance(services_property, property):
            return client.services

        get_services = getattr(client, "get_services", None)
        if get_services is None:
            raise AttributeError(
                f"{client.__class__.__name__} does not expose a services property"
            )

        services = get_services()
        if inspect.isawaitable(services):
            services = await _async_bounded(
                services,
                BLE_OPERATION_TIMEOUT_SECONDS,
                f"GATT service discovery on valve {self._address}",
            )

        return services

    @staticmethod
    def _iter_gatt_services(services) -> Iterable:
        """Yield each service object within a Bleak service collection."""

        if services is None:
            return

        if isinstance(services, dict):
            yield from services.values()
            return

        try:
            iterator = iter(services)
        except TypeError:
            return

        for service in iterator:
            if service is None:
                continue
            yield service

    @classmethod
    def _iter_gatt_characteristics(cls, services) -> Iterable[tuple[object, object]]:
        """Yield (service, characteristic) pairs from a Bleak service collection."""

        for service in cls._iter_gatt_services(services):
            characteristics = getattr(service, "characteristics", ())
            if characteristics is None:
                continue
            for characteristic in characteristics:
                yield service, characteristic

    @classmethod
    def _locate_characteristic(
        cls,
        services,
        *,
        characteristic_uuid: str,
        service_uuid: str | None = None,
        required_properties: Iterable[str] | None = None,
    ) -> tuple[str, set[str], object] | None:
        """Return the characteristic definition matching a UUID."""

        target_uuid = characteristic_uuid.lower()
        target_service_uuid = service_uuid.lower() if service_uuid else None
        required = set(required_properties or ())

        for service, characteristic in cls._iter_gatt_characteristics(services):
            uuid = getattr(characteristic, "uuid", None)
            if not isinstance(uuid, str) or uuid.lower() != target_uuid:
                continue

            if target_service_uuid is not None:
                service_uuid_value = getattr(service, "uuid", None)
                if not isinstance(service_uuid_value, str) or service_uuid_value.lower() != target_service_uuid:
                    continue

            properties = set(getattr(characteristic, "properties", ()) or ())
            if required and not properties.intersection(required):
                continue

            return uuid, properties, characteristic

        return None

    @staticmethod
    def _characteristic_cannot_write_without_response(
        characteristic, properties: set[str]
    ) -> bool:
        """Return ``True`` if the characteristic cannot handle EVB019 payloads."""

        if "write_without_response" not in properties:
            return False

        max_write = getattr(characteristic, "max_write_without_response_size", None)
        if isinstance(max_write, int) and max_write < _EVB019_REQUEST_PACKET_LENGTH:
            return True

        return False

    async def _async_try_start_notify(
        self,
        client: BaseBleakClient,
        uuid: str,
        handler: Callable[[int | str, bytearray], None],
    ) -> bool:
        """Attempt to enable notifications for a characteristic."""

        try:
            awaitable = client.start_notify(uuid, handler)
            if inspect.isawaitable(awaitable):
                await _async_bounded(
                    awaitable,
                    BLE_OPERATION_TIMEOUT_SECONDS,
                    f"notification subscribe on valve {self._address}",
                )
        except TimeoutError:
            return False
        except BLEAK_RETRY_EXCEPTIONS as exc:
            _LOGGER.debug(
                "Failed to subscribe to notifications from %s on valve %s: %s",
                uuid,
                self._address,
                exc,
            )
            return False
        except Exception:  # pragma: no cover - unexpected Bluetooth errors are logged
            _LOGGER.exception(
                "Unexpected error while subscribing to notifications from valve %s characteristic %s",
                self._address,
                uuid,
            )
            return False

        return True

    def _get_dashboard_packet_index(
        self, packet: bytes, existing_packets: Mapping[int, bytes]
    ) -> int | None:
        """Return the packet index for a Dashboard payload.

        ``existing_packets`` contains the indexes that have already been
        collected for the in-progress Dashboard response.  Chandler valves only
        embed packet indexes in the first three packets; the remaining packets
        are inferred using the partial ordering observed in the Android
        implementation.
        """

        length = len(packet)
        if length < 5 or length > 20:
            return None

        opcode = int(ValveRequestCommand.DASHBOARD)
        has_signature = length >= 3 and packet[0] == opcode and packet[1] == opcode

        if has_signature:
            index = packet[2]
            if index not in (0, 1, 2) or index in existing_packets:
                return None

            if index == 0:
                if length < 19 or packet[-1] != 57:
                    return None
            elif index == 1:
                if length < 19 or packet[-1] != 58:
                    return None
            else:  # index == 2
                if length < 20:
                    return None

            return index

        if 2 not in existing_packets:
            return None

        for candidate in range(3, _DASHBOARD_PACKET_COUNT):
            if candidate in existing_packets:
                continue

            if candidate in (3, 4) and length < 20:
                return None

            if candidate == _DASHBOARD_PACKET_COUNT - 1:
                if packet[-1] != 58:
                    return None

            return candidate

        return None

    def _handle_dashboard_packets(self, packets: list[bytes]) -> None:
        """Parse and store the most recent Dashboard response from the valve."""

        if len(packets) != _DASHBOARD_PACKET_COUNT:
            _LOGGER.debug(
                "Valve %s provided incomplete Dashboard response (%d of %d packets)",
                self._address,
                len(packets),
                _DASHBOARD_PACKET_COUNT,
            )
            return

        first, second, third, fourth, fifth, sixth = packets

        if not (
            len(first) >= 19
            and len(second) >= 19
            and len(third) >= 20
            and len(fourth) >= 20
            and len(fifth) >= 20
            and len(sixth) >= 5
        ):
            _LOGGER.debug(
                "Valve %s provided malformed Dashboard packet lengths", self._address
            )
            return

        try:
            time_hour = first[3]
            time_minute = first[4]
            is_pm = first[5] != 0
            battery_capacity = self._calculate_battery_capacity(first[6])
            present_flow = self._decode_flow_value(first, 7)
            water_remaining = self._read_uint16_be(first, 9)
            water_usage = self._read_uint16_be(first, 11)
            peak_flow = self._decode_flow_value(first, 13)
            water_hardness = first[15]
            regeneration_time_hour = first[16]
            regeneration_time_is_pm = first[17] == 1

            flags = first[18]
            shutoff_setting_enabled = bool(flags & 0x01)
            bypass_setting_enabled = bool(flags & 0x02)
            shutoff_active = bool(flags & 0x04)
            bypass_active = bool(flags & 0x08)
            display_off = bool(flags & 0x10)

            filter_backwash = second[3]
            air_recharge = second[4]
            pos_time = second[5]
            pos_option_seconds = second[6]
            regen_cycle_position = second[7]
            regen_active = second[8]
            prefill_soak_mode = bool(second[10] & 0x08)
            soak_timer = second[11]
            is_in_aeration = not bool(second[12] & 0x01)
            tank_in_service = second[18]

            graph_values = (
                list(third[3:20])
                + list(fourth[0:20])
                + list(fifth[0:20])
                + list(sixth[0:5])
            )

            dashboard = ValveDashboardData(
                time_hour=time_hour,
                time_minute=time_minute,
                is_pm=is_pm,
                battery_capacity=battery_capacity,
                present_flow=present_flow,
                water_remaining_until_regeneration=water_remaining,
                water_usage=water_usage,
                peak_flow=peak_flow,
                water_hardness=water_hardness,
                regeneration_time_hour=regeneration_time_hour,
                regeneration_time_is_pm=regeneration_time_is_pm,
                shutoff_setting_enabled=shutoff_setting_enabled,
                bypass_setting_enabled=bypass_setting_enabled,
                shutoff_active=shutoff_active,
                bypass_active=bypass_active,
                display_off=display_off,
                filter_backwash=filter_backwash,
                air_recharge=air_recharge,
                pos_time=pos_time,
                pos_option_seconds=pos_option_seconds,
                regen_cycle_position=regen_cycle_position,
                regen_active=regen_active,
                prefill_soak_mode=prefill_soak_mode,
                soak_timer=soak_timer,
                is_in_aeration=is_in_aeration,
                tank_in_service=tank_in_service,
                graph_usage_ten_gallons=tuple(graph_values),
            )
        except Exception:  # pragma: no cover - parsing errors should be rare
            _LOGGER.exception(
                "Error while parsing Dashboard response from valve %s", self._address
            )
            return

        self._dashboard_data = dashboard
        self._last_dashboard_monotonic = self._monotonic()
        self._last_dashboard_at = dt_util.utcnow()
        self._update_freshness()
        self._notify_dashboard_listeners(dashboard)

    def _notify_dashboard_listeners(
        self, dashboard: ValveDashboardData | None
    ) -> None:
        """Notify registered callbacks about a Dashboard data update."""

        for listener in list(self._dashboard_listeners):
            try:
                listener(dashboard)
            except Exception:  # pragma: no cover - listener failures are logged
                _LOGGER.exception(
                    "Unexpected error in Dashboard listener for valve %s", self._address
                )

    def _notify_authentication_listeners(self) -> None:
        """Notify registered callbacks about authentication lockout changes."""

        locked = self._authentication_failed
        for listener in list(self._authentication_listeners):
            try:
                listener(locked)
            except Exception:  # pragma: no cover - listener failures are logged
                _LOGGER.exception(
                    "Unexpected error in authentication listener for valve %s",
                    self._address,
                )

    @staticmethod
    def _read_uint16_be(packet: bytes, index: int) -> int:
        """Return the unsigned 16-bit integer stored at ``packet[index]``."""

        return (packet[index] << 8) | packet[index + 1]

    @staticmethod
    def _decode_flow_value(packet: bytes, index: int) -> float:
        """Return the flow value encoded in hundredths of a unit."""

        return ValveConnection._read_uint16_be(packet, index) / 100

    @staticmethod
    def _calculate_battery_capacity(raw_value: int) -> int:
        """Convert a raw Dashboard battery value into a capacity percentage."""

        int_value = raw_value * 4 * 0.002 * 11
        if int_value >= 9.5:
            return 100
        if int_value >= 8.91:
            return int(100 - ((9.5 - int_value) * 8.78))
        if int_value >= 8.48:
            return int(94.78 - ((8.91 - int_value) * 30.26))
        if int_value >= 7.43:
            return int(81.84 - ((8.48 - int_value) * 60.47))
        if int_value < 6.5:
            return 0
        return int(18.68 - ((7.43 - int_value) * 20.02))

    def _handle_device_list_packet(self, packet: bytes) -> None:
        """Update internal state from a DeviceList response packet."""

        if not self._is_device_list_packet(packet):
            self._reset_device_list_state()
            return
        self._device_list_is_twin_valve = bool(packet[2])

        decoded_password = self._decode_device_list_password(packet)
        self._device_list_decoded_password = decoded_password
        if decoded_password is not None:
            _LOGGER.debug(
                "Valve %s DeviceList decode -> protocol=%s state=%s auth=%s requires_auth=%s",
                self._address,
                self._authentication_protocol.value,
                decoded_password.state.name,
                decoded_password.authentication_state.name,
                decoded_password.authentication_required,
            )
            if self._has_dashboard_access():
                if self._authentication_failed:
                    _LOGGER.debug(
                        "Valve %s reported authenticated state; clearing previous "
                        "authentication failure",
                        self._address,
                    )
                self._set_authentication_failed(False)
        else:
            _LOGGER.debug(
                "Valve %s DeviceList access unresolved (length=%s, status=%s, protocol=%s)",
                self._address, len(packet), packet[7], self._authentication_protocol.value,
            )

        serial_number = self._extract_serial_number(packet)
        if serial_number is None:
            if self._serial_number is not None:
                _LOGGER.debug(
                    "Clearing stored serial number for valve %s due to empty DeviceList value",
                    self._address,
                )
            self._serial_number = None
            async_update_device_serial_number(
                self._hass, self._config_entry_id, self._address, None
            )
            return

        if serial_number != self._serial_number:
            _LOGGER.debug(
                "Valve %s reported serial number %s", self._address, serial_number
            )
        self._serial_number = serial_number
        async_update_device_serial_number(
            self._hass, self._config_entry_id, self._address, serial_number
        )

    def _extract_serial_number(self, packet: bytes) -> str | None:
        """Return the valve serial number encoded within a DeviceList packet."""

        if len(packet) < 18:
            _LOGGER.debug(
                "DeviceList response from valve %s was too short to contain a serial number",
                self._address,
            )
            return None

        # Chandler Legacy View only targets Evb019 hardware, which always reports
        # serial numbers through the DeviceList response. Classic firmware variants
        # handled elsewhere do not apply to this integration.
        serial = "".join(f"{packet[index]:02X}" for index in range(13, 17)).strip()
        if not serial or serial == _DEFAULT_SERIAL_NUMBER:
            return None

        return serial

    @staticmethod
    def _is_device_list_packet(packet: bytes) -> bool:
        """Return ``True`` if the provided payload matches the DeviceList format."""

        # Require the complete set of fields used by the supported decoder,
        # including serial data. A short echo must not select an auth protocol.
        if len(packet) < _DEVICE_LIST_MIN_RESPONSE_LENGTH:
            return False

        opcode = int(ValveRequestCommand.DEVICE_LIST)
        if packet[0] != opcode or packet[1] != opcode:
            return False

        return packet[2] in (0, 1)

    def _decode_device_list_password(
        self, packet: bytes
    ) -> ValveDecodedPassword | None:
        """Return the decoded passcode reported within a DeviceList payload."""

        if not self._is_device_list_packet(packet):
            return None

        status = packet[7]
        advertisement = self._advertisement
        if self._authentication_protocol is ValveAuthenticationProtocol.UNKNOWN:
            # With classic encoding, status 0 makes the last decoded digit
            # signed(byte_d // 4 + 112), which can never be 0..9. A complete
            # response carrying this challenge identifies counter auth. Status
            # 128 is ambiguous with classic encoding, so it cannot bootstrap it.
            if status == ValveAuthenticationState.NOT_AUTHENTICATED:
                self._set_authentication_protocol(ValveAuthenticationProtocol.COUNTER)
            elif packet[2] == 1:
                self._set_authentication_protocol(ValveAuthenticationProtocol.COUNTER)
            else:
                return None

        if self._authentication_protocol is ValveAuthenticationProtocol.CLASSIC:
            return self._decode_classic_password(
                status, packet[8], packet[9], packet[10], packet[11]
            )

        if ValveAuthenticationState.from_status(status) == ValveAuthenticationState.UNKNOWN:
            self._device_list_authentication_state = ValveAuthenticationState.UNKNOWN
            self._device_list_connection_counter = None
            self._device_list_password_state = ValvePasswordDecodeState.UNKNOWN
            return None

        connection_counter = packet[11]
        if advertisement is not None:
            advertisement.has_connection_counter = True
            if advertisement.authentication_required is None:
                advertisement.authentication_required = True
            previous_counter = advertisement.connection_counter
            if previous_counter != connection_counter:
                _LOGGER.debug(
                    "Valve %s DeviceList connection counter updated from %s to %s",
                    self._address, previous_counter, connection_counter,
                )
            advertisement.connection_counter = connection_counter

        self._device_list_connection_counter = connection_counter
        return self._decode_auth_needed_password(status, connection_counter)

    def _decode_classic_password(
        self, status: int, byte_a: int, byte_b: int, byte_c: int, byte_d: int
    ) -> ValveDecodedPassword:
        """Decode the four-digit passcode embedded in legacy DeviceList packets."""

        self._device_list_connection_counter = None
        b = self._to_signed_byte(status - 112)
        b2 = self._to_signed_byte((byte_d // 4) - b)
        b3 = self._to_signed_byte((byte_c // 3) - b2 - b)
        b4 = self._to_signed_byte((byte_b // 2) - b3 - b2 - b)
        b5 = self._to_signed_byte(byte_a - b4 - b3 - b2 - b)

        digits = [b5, b4, b3, b2]
        valid = all(0 <= digit < 10 for digit in digits)

        if valid:
            passcode = "".join(str(digit) for digit in digits)
            self._device_list_password_retries = 0
            state = ValvePasswordDecodeState.VALID
        else:
            self._device_list_password_retries += 1
            if self._device_list_password_retries >= 3:
                state = ValvePasswordDecodeState.INVALID
            else:
                state = ValvePasswordDecodeState.RETRY
            passcode = ""

        previous_state = self._device_list_password_state
        if (
            previous_state == ValvePasswordDecodeState.INVALID
            and state == ValvePasswordDecodeState.VALID
        ):
            state = ValvePasswordDecodeState.RECOVERED
        elif (
            previous_state == ValvePasswordDecodeState.INVALID
            and state == ValvePasswordDecodeState.INVALID
        ):
            state = ValvePasswordDecodeState.RECOVERY_FAILED

        self._device_list_password_state = state
        self._device_list_authentication_state = ValveAuthenticationState.UNKNOWN

        return ValveDecodedPassword(
            state=state,
            authentication_state=ValveAuthenticationState.UNKNOWN,
            authentication_required=False,
            passcode=passcode,
        )

    def _decode_auth_needed_password(
        self, status: int, connection_counter: int | None
    ) -> ValveDecodedPassword:
        """Return the placeholder response for passcodes requiring authentication."""

        if connection_counter is not None:
            self._device_list_connection_counter = connection_counter

        self._device_list_password_retries = 0
        state = ValvePasswordDecodeState.AUTH_NEEDED
        authentication_state = ValveAuthenticationState.from_status(status & 0xFF)
        self._device_list_authentication_state = authentication_state
        self._device_list_password_state = state

        return ValveDecodedPassword(
            state=state,
            authentication_state=authentication_state,
            authentication_required=True,
            passcode="0000",
        )

    @staticmethod
    def _to_signed_byte(value: int) -> int:
        """Return the provided value constrained to an 8-bit signed range."""

        value &= 0xFF
        if value >= 0x80:
            return value - 0x100
        return value


class ValveConnectionManager:
    """Coordinate periodic Bluetooth polling for discovered valves."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        discovery_manager: ValveDiscoveryManager,
    ) -> None:
        """Initialize the connection manager."""

        self._hass = hass
        self._config_entry = config_entry
        self._discovery_manager = discovery_manager
        self._connections: dict[str, ValveConnection] = {}
        self._remove_listener: CALLBACK_TYPE | None = None
        self._cancel_interval: CALLBACK_TYPE | None = None
        self._startup_unsub: CALLBACK_TYPE | None = None
        self._cancel_watchdog: CALLBACK_TYPE | None = None

    async def async_setup(self) -> None:
        """Begin tracking valves for periodic polling."""

        for advertisement in self._discovery_manager.devices.values():
            connection = self._ensure_connection(advertisement)
            if self._hass.state == CoreState.running:
                connection.schedule_poll()

        self._remove_listener = self._discovery_manager.async_add_listener(
            self._handle_discovery_event
        )
        self._cancel_interval = async_track_time_interval(
            self._hass, self._handle_poll_interval, CONNECTION_POLL_INTERVAL
        )
        self._cancel_watchdog = async_track_time_interval(
            self._hass, self._handle_watchdog, WATCHDOG_INTERVAL
        )

        if self._hass.state != CoreState.running:
            self._startup_unsub = self._hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STARTED, self._handle_home_assistant_started
            )

    async def async_unload(self) -> None:
        """Cancel scheduled work and disconnect listeners."""

        if self._remove_listener is not None:
            self._remove_listener()
            self._remove_listener = None

        if self._cancel_interval is not None:
            self._cancel_interval()
            self._cancel_interval = None

        if self._cancel_watchdog is not None:
            self._cancel_watchdog()
            self._cancel_watchdog = None

        if self._startup_unsub is not None:
            self._startup_unsub()
            self._startup_unsub = None

        await _async_wait_for_cleanup(
            asyncio.create_task(self._async_unload_connections()),
            UNLOAD_TIMEOUT_SECONDS * 4,
        )

    async def _async_unload_connections(self) -> None:
        """Drain all valve cleanup before releasing manager ownership."""

        await asyncio.gather(
            *(connection.async_unload() for connection in self._connections.values()),
            return_exceptions=True,
        )
        self._connections.clear()

    async def async_shutdown(self) -> None:
        """Best-effort cleanup when Home Assistant stops."""

        await self.async_unload()

    @callback
    def _handle_poll_interval(self, _: datetime) -> None:
        """Poll each known valve on a fixed schedule."""

        for connection in self._connections.values():
            connection.schedule_poll()

    @callback
    def _handle_watchdog(self, _: datetime) -> None:
        """Recover stuck work and flag stale data for every known valve."""

        for connection in list(self._connections.values()):
            try:
                connection.watchdog_check()
            except Exception:  # pragma: no cover - the watchdog must keep running
                _LOGGER.exception(
                    "Watchdog failed for valve %s", connection.address
                )

    async def _handle_home_assistant_started(self, _: object) -> None:
        """Trigger an initial poll once Home Assistant startup completes."""

        self._startup_unsub = None
        for connection in self._connections.values():
            connection.schedule_poll()

    @callback
    def _handle_discovery_event(
        self, advertisement: ValveAdvertisement, change: ValveDiscoveryChange
    ) -> None:
        """React to Bluetooth discovery updates from the passive scanner."""

        if change is ValveDiscoveryChange.UNAVAILABLE:
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
            connection = ValveConnection(
                self._hass,
                advertisement.address,
                self._config_entry.entry_id,
                self.get_passcode,
                self._discovery_manager.async_set_connection_state,
            )
            self._connections[advertisement.address] = connection
        connection.update_from_advertisement(advertisement)
        return connection

    def get_connections(self) -> Iterable[ValveConnection]:
        """Return an iterable over the tracked valve connections."""

        return self._connections.values()

    def get_connection(self, address: str) -> ValveConnection | None:
        """Return the connection for a specific valve address, if available."""

        return self._connections.get(address)

    def get_passcode(
        self, address: str | None = None
    ) -> ValvePasscodeConfiguration:
        """Return the configured passcode details for a valve address."""

        overrides = self._config_entry.options.get(CONF_DEVICE_PASSCODES, {})
        if address is not None:
            override = overrides.get(address)
            if override is not None:
                normalized_override = str(override).strip()
                if normalized_override and normalized_override != "0000":
                    return ValvePasscodeConfiguration(
                        value=normalized_override,
                        is_override=True,
                    )

        default_passcode = self._config_entry.data.get(
            CONF_DEFAULT_PASSCODE, DEFAULT_VALVE_PASSCODE
        )
        normalized_default = str(default_passcode).strip()
        if not normalized_default or normalized_default == "0000":
            normalized_default = DEFAULT_VALVE_PASSCODE

        return ValvePasscodeConfiguration(
            value=normalized_default,
            is_override=False,
        )
