"""Simulated GATT transport; production connection and protocol code run unchanged."""

import asyncio
from collections import deque

from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.service import BleakGATTService, BleakGATTServiceCollection

from packets import dashboard_frames, device_list_frame

SERVICE = "00001000-0000-1000-8000-00805f9b34fb"
WRITE = "00001001-0000-1000-8000-00805f9b34fb"
NOTIFY = "00001002-0000-1000-8000-00805f9b34fb"


def authentication_pin(payload, counter):
    """Decode the wire PIN independently of the production encoder/CRC helper."""
    if len(payload) != 20 or payload[:4] != b"ttPA" or counter is None:
        return None
    polynomial, state, nonce = payload[4:7]
    if not polynomial or not state:
        return None

    def remainder(value):
        # Polynomial long division of the seeded byte stream over GF(2).
        # Production uses a bit-by-bit shift register instead.
        divisor = 0x100 | polynomial
        while value.bit_length() > 8:
            value ^= divisor << (value.bit_length() - 9)
        return value

    state = remainder((state << 8) | nonce)
    previous = counter ^ state
    digits = []
    for encoded in payload[7:11]:
        state = remainder((state << 8) | previous)
        digits.append(encoded ^ state)
        previous = encoded
    return "".join(map(str, digits)) if all(digit < 10 for digit in digits) else None


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
        self.pin = "1234"
        self.authentication_required = True
        self.connection_counter = 23
        self.firmware_version = None
        self.is_twin_valve = False
        self.legacy_reserved_counter_byte = 42
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
        self.connection_counter = valve.connection_counter
        valve.connection_counter = (valve.connection_counter + 1) & 255
        self.challenge = None
        self.dashboard_access = not valve.authentication_required
        # These Bleak data objects perform no host I/O. Keep the real service
        # representation so polling cannot rely on a test-only list contract.
        self._services = BleakGATTServiceCollection()
        service = BleakGATTService(obj=None, handle=1, uuid=SERVICE)
        self._services.add_service(service)
        for handle, uuid, properties in ((2, WRITE, ["write"]), (3, NOTIFY, ["notify"])):
            self._services.add_characteristic(BleakGATTCharacteristic(
                obj=None, handle=handle, uuid=uuid, properties=properties,
                max_write_without_response_size=lambda: 20, service=service,
            ))

    @property
    def services(self):
        return self._services

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
        if len(packet) >= 18 and packet[:2] == b"tt" and packet[2] in (0, 1):
            if packet[7] == 0:
                # CsDeviceListPacket.update uses the advertisement counter for
                # C4.19; byte 11 is not a challenge until C4.20 (or on twins).
                self.challenge = (self.connection_counter if self.counter_in_advertisement
                                  else packet[11])
                self.dashboard_access = not self.valve.authentication_required
            elif packet[7] == 128:
                # Scripted DeviceList status 128 explicitly represents a
                # preauthenticated session; it is never inherited on reconnect.
                self.challenge = None
                self.dashboard_access = True
        characteristic = self.services.get_characteristic(NOTIFY)
        assert characteristic is not None
        for callback in list(self.notifications.values()):
            callback(characteristic, bytearray(packet))

    @property
    def counter_in_advertisement(self):
        return self.valve.firmware_version == 419 and not self.valve.is_twin_valve

    def device_list_reply(self, status, counter):
        return device_list_frame(
            status=status,
            counter=self.valve.legacy_reserved_counter_byte if self.counter_in_advertisement else counter,
            firmware=self.valve.firmware_version, twin=self.valve.is_twin_valve,
        )

    async def write_gatt_char(self, uuid, payload, *, response):
        self.require_connected()
        assert uuid == WRITE and response
        payload = bytes(payload)
        self.valve.writes.append((self, payload))
        if payload[:4] == b"ttPA":
            operation = "authenticate"
            self.valve.authentication_payloads.append(payload)
            counter = self.challenge if self.challenge is not None else self.connection_counter
            if authentication_pin(payload, self.challenge) != self.valve.pin:
                self.dashboard_access = False
                replies = [self.device_list_reply(0, counter)]
            else:
                replies = (self.valve.auth_replies.popleft() if self.valve.auth_replies
                           else [self.device_list_reply(128, (counter + 1) & 255)])
        elif payload == b"t" * 20:
            operation = "device_list"
            replies = (self.valve.device_lists.popleft() if self.valve.device_lists
                       else [self.device_list_reply(0, self.connection_counter)])
        elif payload == b"u" * 20:
            operation = "dashboard"
            self.valve.dashboard_requests += 1
            replies = ((self.valve.dashboards.popleft() if self.valve.dashboards else dashboard_frames())
                       if self.dashboard_access else [])
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
        self.challenge = None
        self.dashboard_access = False


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
