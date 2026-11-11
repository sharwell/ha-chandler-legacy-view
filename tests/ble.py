"""Simulated GATT transport; production connection and protocol code run unchanged."""

import asyncio
import json
from collections import deque

from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.service import BleakGATTService, BleakGATTServiceCollection

from packets import dashboard_frames, device_list_frame
from evb034_packets import crc16, frames, password_pin

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
        self.ignore_cancellation = False

    def block(self):
        self.release.clear()
        return self

    async def run(self):
        self.started.set()
        while True:
            try:
                await self.release.wait()
                break
            except asyncio.CancelledError:
                self.cancelled = True
                if not self.ignore_cancellation:
                    raise
        if self.failure is not None:
            raise self.failure


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
        self.mtu_size = 23
        self.identification_packets = deque()
        self.chunk_acknowledgements = deque()
        self.id_acknowledgements = deque()
        self.timeout_responses = deque()
        self.packet_requests = []
        self.dashboard_notifications = 0
        self.stream_enabled = True
        self.display_off = False
        self.accept_display_settings = True
        self.report_display_off = True
        self.display_writes = []
        self.status_bits = 0
        self.legacy_service_discovery = False
        self.operations = {name: Operation() for name in
                           ("connect", "identify", "device_list", "authenticate", "dashboard", "reset", "disconnect", "timeout",
                            "start_notify", "stop_notify", "services", "display_setting")}

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
        self.mtu_size = valve.mtu_size
        self.packet_buffer = bytearray()
        self.peer_tasks = []
        self.peer_ack = asyncio.Queue()
        self.peer_outgoing = asyncio.Queue()
        self.peer_access = asyncio.Event()
        self.peer_latest_data = {"dwu": 567}
        if valve.report_display_off:
            self.peer_latest_data["asdo"] = int(valve.display_off)
        if not valve.authentication_required:
            self.peer_access.set()
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
        await self.valve.operations["start_notify"].run()
        self.require_connected()
        assert uuid == NOTIFY
        assert uuid not in self.notifications
        self.notifications[uuid] = callback
        self.notification_history.append(callback)

    async def stop_notify(self, uuid):
        self.require_connected()
        await self.valve.operations["stop_notify"].run()
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
        packet = bytearray(device_list_frame(
            status=status,
            counter=self.valve.legacy_reserved_counter_byte if self.counter_in_advertisement else counter,
            firmware=self.valve.firmware_version, twin=self.valve.is_twin_valve,
        ))
        if (self.valve.firmware_version is not None and self.valve.firmware_version < 419
                and not self.valve.is_twin_valve and not self.valve.authentication_required):
            assert self.valve.pin == "1234"
            packet[7:12] = bytes.fromhex("5cf6ead9c0")
        return bytes(packet)

    async def write_gatt_char(self, uuid, payload, *, response):
        self.require_connected()
        assert uuid == WRITE and response
        payload = bytes(payload)
        self.valve.writes.append((self, payload))
        if self.valve.firmware_version is not None and self.valve.firmware_version >= 600:
            await self.write_evb034(payload)
            return
        if payload == b"\xea":
            # Evb019 firmware does not understand the newer-family probe.
            return
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
            default = dashboard_frames()
            first = bytearray(default[0])
            first[18] = self.valve.status_bits | (0x10 if self.valve.display_off else 0)
            default[0] = bytes(first)
            replies = ((self.valve.dashboards.popleft() if self.valve.dashboards else default)
                       if self.dashboard_access and (self.valve.report_display_off or not self.valve.display_writes) else [])
        elif (len(payload) == 20 and payload[:13] == b"v" * 13
              and payload[13] == 0x47 and payload[14] in (0, 1) and payload[15:] == b"v" * 5):
            await self.write_display_setting(payload[14])
            return
        elif payload == b"r" * 20:
            operation, replies = "reset", []
        else:
            raise AssertionError(f"Unexpected outbound command {payload[:2].hex()}")
        await self.valve.operations[operation].run()
        for packet in replies:
            self.notify(packet)

    async def write_display_setting(self, value):
        """Model one persisted field without mutating the other status bits."""
        self.valve.display_writes.append((self, value))
        await self.valve.operations["display_setting"].run()
        self.require_connected()
        if self.dashboard_access and self.valve.accept_display_settings:
            self.valve.display_off = bool(value)

    async def write_evb034(self, payload):
        """Reference peer: EA starts notifications; raw PIN precedes framed JSON."""
        if payload in (b"t" * 20, b"u" * 20, b"r" * 20):
            # Legacy probing is allowed only before identifying this peer.
            assert not self.peer_tasks, "C6.x never polls with Evb019 opcodes"
            return
        if payload == b"\xea":
            await self.valve.operations["identify"].run()
            ack = (self.valve.id_acknowledgements.popleft()
                   if self.valve.id_acknowledgements else b"\xcc")
            if ack:
                self.notify(ack)
            if ack != b"\xcc" or self.peer_tasks:
                return
            self.challenge = self.connection_counter
            self.peer_tasks = [asyncio.create_task(self._peer_sender()),
                               asyncio.create_task(self._peer_stream())]
            return
        if payload in (b"\xcc", b"\xc8"):
            self.peer_ack.put_nowait(payload)
            return
        if payload in (b"\xe0", b"\xf0"):
            return
        if payload in (b"\xc2", b"\xc3"):
            await self.valve.operations["timeout"].run()
            # A scripted timeout exchange supplies each peer status explicitly;
            # do not manufacture an unbounded C2/C3 echo conversation.
            if self.valve.timeout_responses:
                reply = self.valve.timeout_responses.popleft()
                if reply:
                    self.notify(reply)
            return
        if not self.dashboard_access:
            assert len(payload) == 20, "Initial password is a direct ATT write"
            await self.valve.operations["authenticate"].run()
            self.valve.authentication_payloads.append(payload)
            pin = password_pin(payload, self.challenge)
            accepted = pin == self.valve.pin
            scripted = bool(self.valve.auth_replies)
            reply = (self.valve.auth_replies.popleft() if scripted else
                     frames({"as": 2 if accepted else 1}, min(512, self.mtu_size - 3)))
            state = self._peer_packet(reply).get("as")
            self.dashboard_access = accepted and state == 2
            await self.peer_outgoing.put((reply, scripted))
            if self.dashboard_access:
                self.peer_access.set()
            return
        assert len(payload) <= min(512, self.mtu_size - 3)
        assert int.from_bytes(payload[-2:], "little") == crc16(payload[:-2])
        ack = (self.valve.chunk_acknowledgements.popleft()
               if self.valve.chunk_acknowledgements else b"\xcc")
        if ack != b"\xcc":
            if ack:
                self.notify(ack)
            return
        if payload[0] & 0x80:
            self.packet_buffer.clear()
        self.packet_buffer.extend(payload[1:-2])
        self.notify(ack)
        if not payload[0] & 0x40:
            return
        request = bytes(self.packet_buffer)
        self.packet_buffer.clear()
        self.valve.packet_requests.append(request)
        command = json.loads(request)
        # Packet-session tests can send several app configuration fields in one
        # JSON message; battery sampling and display-off have modeled responses.
        if command.get("gbre") == 1:
            await self.peer_outgoing.put((frames({"gbre": 1}, min(512, self.mtu_size - 3)), False))
        if "asdo" in command:
            assert type(command["asdo"]) is int and command["asdo"] in (0, 1)
            await self.write_display_setting(command["asdo"])
            if self.valve.report_display_off:
                self.peer_latest_data["asdo"] = int(self.valve.display_off)
                await self.peer_outgoing.put((frames({"asdo": int(self.valve.display_off)}, min(512, self.mtu_size - 3)), False))
            else:
                self.peer_latest_data.pop("asdo", None)

    @staticmethod
    def _peer_packet(reply):
        try:
            return json.loads(b"".join(frame[1:-2] for frame in reply))
        except (ValueError, TypeError, UnicodeError):
            return {}

    async def _peer_sender(self):
        try:
            while True:
                reply, scripted = await self.peer_outgoing.get()
                fault_delivery = scripted and any(
                    len(frame) < 3 or len(frame) > min(512, self.mtu_size - 3)
                    or int.from_bytes(frame[-2:], "little") != crc16(frame[:-2])
                    for frame in reply
                )
                for frame in reply:
                    if fault_delivery:
                        # Fault scripts can deliberately violate framing/ordering
                        # at the external notification boundary.
                        self.notify(frame)
                        await asyncio.sleep(0)
                        continue
                    while not self.peer_ack.empty():
                        self.peer_ack.get_nowait()
                    for attempt in range(2):
                        self.notify(frame)
                        try:
                            async with asyncio.timeout(3):
                                ack = await self.peer_ack.get()
                        except TimeoutError:
                            ack = b"\xc8"
                        if ack == b"\xcc":
                            break
                        if attempt == 1:
                            self.is_connected = False
                            return
        except asyncio.CancelledError:
            raise

    async def _peer_stream(self):
        # CsDeviceListFragment.lambda$writeData$8 skips C6.x DeviceList writes.
        # EA therefore produces identification without any client read request.
        await self.valve.operations["device_list"].run()
        default = {"dlf": self.valve.firmware_version, "dlc": self.connection_counter,
                   "as": 1 if self.valve.authentication_required else 2,
                   "dlvt": 1, "dli": 0, "dlic": 0, "gbre": 1}
        scripted = bool(self.valve.identification_packets)
        reply = (self.valve.identification_packets.popleft() if scripted
                 else frames(default, min(512, self.mtu_size - 3)))
        await self.peer_outgoing.put((reply, scripted))
        while True:
            await self.peer_access.wait()
            await self.valve.operations["dashboard"].run()
            if self.valve.stream_enabled:
                scripted = bool(self.valve.dashboards)
                reply = (self.valve.dashboards.popleft() if scripted else
                         frames(self.peer_latest_data, min(512, self.mtu_size - 3)))
                values = self._peer_packet(reply)
                self.peer_latest_data.update({key: value for key, value in values.items() if key != "as"})
                if values.get("as") == 1:
                    self.dashboard_access = False
                    self.peer_access.clear()
                self.valve.dashboard_notifications += 1
                await self.peer_outgoing.put((reply, scripted))
            await asyncio.sleep(1)

    async def disconnect(self):
        self.disconnects += 1
        for task in self.peer_tasks:
            task.cancel()
        if self.peer_tasks:
            await asyncio.gather(*self.peer_tasks, return_exceptions=True)
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
        client = LegacyServiceClient(valve) if valve.legacy_service_discovery else Client(valve)
        valve.clients.append(client)
        return client


class LegacyServiceClient(Client):
    """Model older Bleak's asynchronous service discovery API."""

    services = None

    async def get_services(self):
        await self.valve.operations["services"].run()
        self.require_connected()
        return self._services

    def notify(self, packet):
        # Wire delivery still uses the external service graph, independent of
        # how the client exposes asynchronous discovery to its caller.
        services = self._services
        self.services = services
        try:
            super().notify(packet)
        finally:
            self.services = None
