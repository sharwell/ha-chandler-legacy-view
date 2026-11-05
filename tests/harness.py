"""Shared fixtures for failure-injection tests of the valve connection layer.

``FakeValveClient`` stands in for a Bleak client connected to an EVB019 valve. It
answers real DeviceList and Dashboard requests with real packets, and each GATT
operation can be told to succeed, raise, hang, or "zombie" (swallow
cancellation, as a wedged Bluetooth stack or proxy can). That reproduces the
field failure where a stalled call held a valve's poll lock for days.

Tests wait on conditions with ``wait_until`` rather than sleeping, and drive
the watchdog with ``FakeClock`` so nothing depends on wall-clock time.
"""

from __future__ import annotations

import asyncio
from collections import Counter
import logging
from datetime import timedelta
from enum import Enum
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from test_persistent_connection import connection_module

NOTIFY_UUID = "00001002-0000-1000-8000-00805f9b34fb"
WRITE_UUID = "00001001-0000-1000-8000-00805f9b34fb"
SERVICE_UUID = "00001000-0000-1000-8000-00805f9b34fb"

DEVICE_LIST = int(connection_module.ValveRequestCommand.DEVICE_LIST)
DASHBOARD = int(connection_module.ValveRequestCommand.DASHBOARD)
RESET = int(connection_module.ValveRequestCommand.RESET)

# Short real-time limits so failure paths finish in milliseconds. Each is far
# below the one-second ceiling the tests give any single scenario.
FAST_LIMITS = {
    "BLE_OPERATION_TIMEOUT_SECONDS": 0.05,
    "POLL_FETCH_TIMEOUT_SECONDS": 0.4,
    "PERSISTENT_REQUEST_TIMEOUT_SECONDS": 0.2,
    "CONNECTION_TIMEOUT_SECONDS": 0.2,
    "UNLOAD_TIMEOUT_SECONDS": 0.3,
    "MIN_PERSISTENT_POLL_INTERVAL_SECONDS": 0,
    "_DEVICE_LIST_RESPONSE_TIMEOUT_SECONDS": 0.05,
    "_DASHBOARD_RESPONSE_TIMEOUT_SECONDS": 0.05,
    "_RESET_BUFFER_TIMEOUT_SECONDS": 0.05,
    "_DISCONNECT_TIMEOUT_SECONDS": 0.1,
    "CONNECTION_MIN_RETRY_INTERVAL": timedelta(0),
    "PERSISTENT_RECONNECT_DELAY_SECONDS": 0,
}


def device_list_frame(*, status: int = 128, counter: int = 23) -> bytes:
    """Build a synthetic DeviceList reply; status 128 means authenticated."""

    packet = bytearray(20)
    packet[0:2] = b"tt"
    packet[7] = status
    packet[11] = counter
    packet[13:17] = b"\x01\x02\x03\x04"
    return bytes(packet)


def dashboard_frames(*, usage: int = 567, flow_raw: int = 0) -> list[bytes]:
    """Build a complete six-packet dashboard reporting ``usage`` gallons."""

    first, second, third = (bytearray(20) for _ in range(3))
    first[0:5] = b"uu\x00\x07\x17"
    first[7] = flow_raw
    first[11:13] = usage.to_bytes(2, "big")
    first[-1] = 57
    second[0:3] = b"uu\x01"
    second[-1] = 58
    third[0:3] = b"uu\x02"
    return [bytes(first), bytes(second), bytes(third), bytes(20), bytes(20), bytes(5) + b":"]


class Mode(Enum):
    """How a fake GATT operation behaves."""

    OK = "ok"
    RAISE = "raise"
    HANG = "hang"  # waits forever, but honors cancellation and timeouts
    ZOMBIE = "zombie"  # waits forever and swallows cancellation


class FakeValveClient:
    """A Bleak-shaped client with a scriptable failure mode per operation."""

    def __init__(self) -> None:
        self.is_connected = True
        self.modes: dict[str, Mode] = {}
        self.calls: Counter[str] = Counter()
        self.requests: list[int] = []
        self.handler = None
        self.answer = True
        self.usage = 567
        self.swallowed_cancellations = 0
        self.disconnected = False
        self._stuck = asyncio.Event()

    @property
    def services(self):
        return [
            SimpleNamespace(
                uuid=SERVICE_UUID,
                characteristics=[
                    SimpleNamespace(uuid=WRITE_UUID, properties=["write"]),
                    SimpleNamespace(uuid=NOTIFY_UUID, properties=["notify"]),
                ],
            )
        ]

    def release(self) -> None:
        """Let every hung or zombie operation finish, so tests can clean up."""

        self._stuck.set()

    async def _behave(self, operation: str) -> None:
        self.calls[operation] += 1
        mode = self.modes.get(operation, Mode.OK)
        if mode is Mode.OK:
            return
        if mode is Mode.RAISE:
            raise OSError(f"{operation} failed")
        if mode is Mode.HANG:
            await self._stuck.wait()
            return
        while not self._stuck.is_set():
            try:
                await self._stuck.wait()
            except asyncio.CancelledError:
                self.swallowed_cancellations += 1

    async def write_gatt_char(self, uuid, payload, response=True) -> None:
        await self._behave("write")
        opcode = payload[0]
        self.requests.append(opcode)
        if not self.answer or self.handler is None:
            return
        if opcode == DEVICE_LIST:
            self.handler(NOTIFY_UUID, bytearray(device_list_frame()))
        elif opcode == DASHBOARD:
            for packet in dashboard_frames(usage=self.usage):
                self.handler(NOTIFY_UUID, bytearray(packet))

    async def start_notify(self, uuid, handler) -> None:
        await self._behave("start_notify")
        self.handler = handler

    async def stop_notify(self, uuid) -> None:
        await self._behave("stop_notify")
        self.handler = None

    async def disconnect(self) -> None:
        await self._behave("disconnect")
        self.is_connected = False
        self.disconnected = True


class AsyncServicesClient(FakeValveClient):
    """Variant exposing ``get_services()`` instead of a ``services`` property."""

    services = None  # not a property, so the connection awaits get_services()

    async def get_services(self):
        await self._behave("get_services")
        return FakeValveClient.services.fget(self)


class FakeClock:
    """A manually advanced monotonic clock for the watchdog's bookkeeping."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


async def wait_until(predicate, *, timeout: float = 1.0, message: str = "") -> None:
    """Wait for a condition instead of sleeping for a guessed duration."""

    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0)
    if message:
        assert predicate(), message


def make_advertisement(address: str = "test-valve"):
    """Return an advertisement that makes the connection treat the valve as known."""

    return connection_module.ValveAdvertisement(
        address=address, name="CS_Meter_Soft", rssi=-60,
        manufacturer_data={}, service_data={},
        firmware_major=4, firmware_minor=22, firmware_version=422, model="Evb019",
        manufacturer_data_complete=True, valve_data_parsed=True,
        has_connection_counter=True, authentication_required=True,
    )


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


class CapturedLogs:
    """Collect the connection module's log records instead of printing them."""

    def __init__(self, handler: _ListHandler) -> None:
        self._handler = handler

    def messages(self, level: int = logging.WARNING) -> list[str]:
        return [
            record.getMessage()
            for record in self._handler.records
            if record.levelno >= level
        ]

    def clear(self) -> None:
        self._handler.records.clear()

    def contains(self, text: str, level: int = logging.WARNING) -> bool:
        return any(text in message for message in self.messages(level))


def _capture_logs(case: unittest.TestCase) -> CapturedLogs:
    logger = logging.getLogger(connection_module.__name__)
    handler = _ListHandler()
    previous = (logger.propagate, logger.level)
    logger.addHandler(handler)
    logger.propagate = False
    logger.setLevel(logging.DEBUG)

    def restore() -> None:
        logger.removeHandler(handler)
        logger.propagate, level = previous
        logger.setLevel(level)

    case.addCleanup(restore)
    return CapturedLogs(handler)


class ConnectionTestCase(unittest.IsolatedAsyncioTestCase):
    """A real ``ValveConnection`` wired to a scriptable fake valve.

    Attributes:
        connection: the connection under test.
        clients: every client handed out by ``establish_connection``, in order.
        clock: the fake monotonic clock driving watchdog bookkeeping.
    """

    stub_schedule_poll = False

    async def asyncSetUp(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.tasks: list[asyncio.Task] = []
        self.hass = SimpleNamespace(
            loop=self.loop,
            state=connection_module.CoreState.running,
            async_create_task=self._create_task,
        )
        self.connection_state = []
        self.connection = connection_module.ValveConnection(
            self.hass, "test-valve", "test-entry",
            passcode_getter=lambda _: connection_module.ValvePasscodeConfiguration(
                value="1234", is_override=False
            ),
            connection_state_callback=lambda address, up: self.connection_state.append(up),
        )
        self.connection.update_from_advertisement(make_advertisement())
        self.clock = FakeClock()
        # The connection read the real clock when it was built; rebase it.
        self.connection._created_at = self.clock.now
        self.log = _capture_logs(self)
        self.clients: list[FakeValveClient] = []
        self.next_client_factory = FakeValveClient
        self.establish = AsyncMock(side_effect=self._establish)

        for name, value in FAST_LIMITS.items():
            patcher = patch.object(connection_module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for patcher in (
            patch.object(connection_module, "establish_connection", self.establish),
            patch.object(
                connection_module.ValveConnection, "_monotonic",
                staticmethod(self.clock),
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addAsyncCleanup(self._cleanup)

    def _create_task(self, coroutine) -> asyncio.Task:
        task = self.loop.create_task(coroutine)
        self.tasks.append(task)
        return task

    async def _establish(self, *args, **kwargs) -> FakeValveClient:
        client = self.next_client_factory()
        self.clients.append(client)
        return client

    async def _cleanup(self) -> None:
        """Release anything still hung, then drain every task the test started."""

        for client in self.clients:
            client.release()
        self.connection._persistent_connection_enabled = False
        with_timeout = []
        for task in list(self.tasks):
            if not task.done():
                task.cancel()
            with_timeout.append(task)
        if with_timeout:
            async with asyncio.timeout(2):
                await asyncio.gather(*with_timeout, return_exceptions=True)
        async with asyncio.timeout(2):
            await self.connection.async_unload()

    @property
    def client(self) -> FakeValveClient:
        return self.clients[-1]

    def start_poll(self) -> asyncio.Task:
        return self._create_task(self.connection.async_poll())

    async def poll_to_completion(self, *, limit: float = 1.0) -> None:
        """Run one poll and require that it finishes promptly."""

        async with asyncio.timeout(limit):
            await self.connection.async_poll()

    def assert_idle(self) -> None:
        """No poll lock held and no poll task or persistent task outstanding."""

        self.assertFalse(self.connection._lock.locked())
        self.assertIsNone(self.connection._poll_task)
        self.assertIsNone(self.connection._persistent_task)
