"""Simulated GATT transport; production connection and protocol code run unchanged."""

import asyncio
from collections import deque
from dataclasses import dataclass

from packets import dashboard_frames, device_list_frame

SERVICE = "00001000-0000-1000-8000-00805f9b34fb"
WRITE = "00001001-0000-1000-8000-00805f9b34fb"
NOTIFY = "00001002-0000-1000-8000-00805f9b34fb"


@dataclass
class Characteristic:
    uuid: str
    properties: tuple[str, ...]


@dataclass
class Service:
    uuid: str
    characteristics: list[Characteristic]


class Operation:
    """A controllable external operation, including cancellation and completion."""

    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.cancelled = False
        self.failure = None

    def block(self):
        self.release.clear()
        return self

    async def run(self):
        self.started.set()
        try:
            await self.release.wait()
            if self.failure is not None:
                raise self.failure
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class Valve:
    """Script valve replies at the wire boundary, shared across distinct sessions."""

    def __init__(self, address="valve-a"):
        self.address = address
        self.device_lists = deque()
        self.auth_replies = deque()
        self.dashboards = deque()
        self.authentication_payloads = []
        self.dashboard_requests = 0
        self.writes = []
        self.clients = []
        self.operations = {name: Operation() for name in
                           ("connect", "device_list", "authenticate", "dashboard", "reset", "disconnect")}

    def authenticated(self, counter=23):
        self.device_lists.append([device_list_frame(status=0, counter=counter)])
        self.auth_replies.append([device_list_frame(status=128, counter=(counter + 1) & 255)])


class Client:
    def __init__(self, valve):
        self.valve = valve
        self.is_connected = True
        self.notifications = {}
        self.notification_history = []
        self.disconnects = 0

    @property
    def services(self):
        return [Service(SERVICE, [Characteristic(WRITE, ("write",)),
                                  Characteristic(NOTIFY, ("notify",))])]

    def require_connected(self):
        if not self.is_connected:
            raise OSError("GATT operation on disconnected session")

    async def start_notify(self, uuid, callback):
        self.require_connected()
        assert uuid == NOTIFY
        assert uuid not in self.notifications
        self.notifications[uuid] = callback
        self.notification_history.append(callback)

    async def stop_notify(self, uuid):
        self.require_connected()
        del self.notifications[uuid]

    def notify(self, packet):
        self.require_connected()
        for callback in list(self.notifications.values()):
            callback(NOTIFY, bytearray(packet))

    async def write_gatt_char(self, uuid, payload, *, response):
        self.require_connected()
        assert uuid == WRITE and response
        payload = bytes(payload)
        self.valve.writes.append((self, payload))
        if payload[:4] == b"ttPA":
            operation = "authenticate"
            self.valve.authentication_payloads.append(payload)
            replies = self.valve.auth_replies.popleft() if self.valve.auth_replies else [device_list_frame(status=128)]
        elif payload == b"t" * 20:
            operation = "device_list"
            replies = self.valve.device_lists.popleft() if self.valve.device_lists else [device_list_frame(status=0)]
        elif payload == b"u" * 20:
            operation = "dashboard"
            self.valve.dashboard_requests += 1
            replies = self.valve.dashboards.popleft() if self.valve.dashboards else dashboard_frames()
        elif payload == b"r" * 20:
            operation, replies = "reset", []
        else:
            raise AssertionError(f"Unexpected outbound command {payload[:2].hex()}")
        await self.valve.operations[operation].run()
        for packet in replies:
            self.notify(packet)

    async def disconnect(self):
        self.disconnects += 1
        await self.valve.operations["disconnect"].run()
        self.notifications.clear()
        self.is_connected = False


class Transport:
    """Enforce session ownership at the simulated connection boundary."""

    def __init__(self, scanner):
        self.scanner = scanner
        self.valves = {}
        self.attempts = []

    def valve(self, address="valve-a"):
        return self.valves.setdefault(address, Valve(address))

    def resolve(self, hass, address, connectable=True):
        info = self.scanner.history.get(address)
        return info.device if info else None

    async def connect(self, client_type, device, name):
        valve = self.valve(device.address)
        self.attempts.append(device.address)
        if any(client.is_connected for client in valve.clients):
            raise OSError("Previous simulated session still owns the connection")
        await valve.operations["connect"].run()
        client = Client(valve)
        valve.clients.append(client)
        return client
