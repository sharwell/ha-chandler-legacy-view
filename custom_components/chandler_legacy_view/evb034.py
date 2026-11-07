"""Connection-owned Evb034 wire exchange from Water System 6.18.2.

See CsBleQueueProcessor.chunkPacket/processResponse/processDataPacket and
CsBleLeManager.configureConnection$lambda$7 (ATT MTU minus three bytes).
"""

from __future__ import annotations

import asyncio
from binascii import crc_hqx
import json
from collections import deque
from collections.abc import Awaitable, Callable

ACK = b"\xcc"
NAK = b"\xc8"
ID = b"\xea"
TIMEOUT = 3.0
MAX_PACKET_BYTES = 2048
PACKET_ATTEMPTS = 2


class Evb034ProtocolError(ValueError):
    """An exchange cannot safely produce a packet update."""


class ReceivedPacket(dict):
    """Carry each field's monotonic receipt time through partial coalescing."""

    def __init__(self):
        super().__init__()
        self.confirmed_at: dict[str, float] = {}

    def update(self, other):
        super().update(other)
        if isinstance(other, ReceivedPacket):
            self.confirmed_at.update(other.confirmed_at)


def frame_payload(payload: bytes, size: int) -> tuple[bytes, ...]:
    """Add first/last flags and a little-endian CCITT CRC to each chunk."""
    if not 14 <= size <= 512 or not payload or len(payload) > MAX_PACKET_BYTES:
        raise Evb034ProtocolError("Invalid packet or negotiated write size")
    chunks = []
    for offset in range(0, len(payload), size - 3):
        end = offset + size - 3
        header = (0x80 if offset == 0 else 0) | (0x40 if end >= len(payload) else 0)
        body = bytes((header,)) + payload[offset:end]
        chunks.append(body + crc_hqx(body, 0xFFFF).to_bytes(2, "little"))
    return tuple(chunks)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Evb034ProtocolError("Duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value):
    raise Evb034ProtocolError("Non-finite JSON number")


class Evb034Session:
    """Receive and ACK independently of HA's publication interval.

    CsBleViewModel.onBleDeviceFullConnected sends EA; the initial PIN is raw.
    Only authenticated configuration JSON uses CsBleQueueProcessor framing.
    """

    def __init__(
        self, write: Callable[[bytes], Awaitable[None]], size: int, *,
        on_packet: Callable[[ReceivedPacket], None] | None = None,
        on_failure: Callable[[Exception], None] | None = None,
    ) -> None:
        self.write_size = size
        self._write = write
        self._queue: asyncio.Queue[tuple[int, bytes, float]] = asyncio.Queue(maxsize=128)
        self._packets: deque[tuple[int, dict]] = deque()
        self._acks: deque[tuple[int, bytes]] = deque()
        self._notification_serial = 0
        self._ready = asyncio.Event()
        self._failed_event = asyncio.Event()
        self._authenticated = False
        self._command_active = False
        self._timeout_retries = 0
        self._operation_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._closed = False
        self._failure: Exception | None = None
        self._reader: asyncio.Task | None = None
        self._buffer: bytearray | None = None
        self._on_packet = on_packet
        self._on_failure = on_failure

    def notification(self, _sender, data: bytearray) -> None:
        """Ignore callbacks after this client relinquishes ownership."""
        if self._closed or self._failure is not None:
            return
        self._notification_serial += 1
        try:
            self._queue.put_nowait((self._notification_serial, bytes(data), asyncio.get_running_loop().time()))
        except asyncio.QueueFull:
            self._fail(Evb034ProtocolError("Receive queue full"))

    def _fail(self, error: Exception) -> None:
        if self._failure is not None:
            return
        self._failure = error
        self._ready.set()
        self._failed_event.set()
        if self._on_failure is not None:
            self._on_failure(error)

    def mark_authenticated(self) -> None:
        """Require continued access on this confirmed connection."""
        self._authenticated = True

    async def wait_interval(self, seconds: float) -> None:
        """Wait for HA's interval, waking immediately if access/transport fails."""
        self.check_health()
        try:
            async with asyncio.timeout(seconds):
                await self._failed_event.wait()
        except TimeoutError:
            return
        self.check_health()

    def check_health(self) -> None:
        """Report receiver failure even when no consumer is awaiting data."""
        if self._failure is not None:
            raise Evb034ProtocolError("Notification receiver failed") from self._failure
        if self._closed:
            raise Evb034ProtocolError("Session closed")

    async def close(self) -> None:
        """Invalidate callbacks and drain the owned receiver task."""
        self._closed = True
        self._ready.set()
        if self._reader is not None:
            self._reader.cancel()
            try:
                await self._reader
            except asyncio.CancelledError:
                pass
            self._reader = None
        self._packets.clear()
        self._acks.clear()
        self._buffer = None
        while not self._queue.empty():
            self._queue.get_nowait()

    async def _send(self, payload: bytes) -> None:
        async with self._write_lock:
            self.check_health()
            async with asyncio.timeout(TIMEOUT):
                await self._write(payload)

    async def _wait_ready(self, values: deque, timeout: float):
        async with asyncio.timeout(timeout):
            while True:
                self.check_health()
                if values:
                    return values.popleft()
                self._ready.clear()
                await self._ready.wait()

    async def identify(self) -> dict:
        """Receive identification after EA, without an Evb019 read command."""
        if self._reader is not None:
            raise Evb034ProtocolError("Session already identified")
        self._reader = asyncio.create_task(self._receive_loop())
        packet = ReceivedPacket()
        for attempt in range(PACKET_ATTEMPTS):
            await self._send(ID)
            try:
                async with asyncio.timeout(TIMEOUT * 3):
                    while True:
                        packet.update(await self.next_packet())
                        if "dlf" not in packet:
                            continue
                        firmware = packet["dlf"]
                        if type(firmware) is not int or not 600 <= firmware <= 9999:
                            raise Evb034ProtocolError("Invalid Evb034 identification")
                        return packet
            except TimeoutError:
                if attempt + 1 == PACKET_ATTEMPTS:
                    raise
        raise Evb034ProtocolError("Identification missing")

    async def authenticate(self, payload: bytes) -> dict:
        """Match the direct pre-authentication write in CsBleViewModel.write."""
        if len(payload) != min(20, self.write_size):
            raise Evb034ProtocolError("Invalid authentication buffer length")
        async with self._operation_lock:
            after = self._notification_serial
            await self._send(payload)
            async with asyncio.timeout(TIMEOUT * 3):
                while True:
                    packet = await self.next_packet(after=after)
                    if "as" in packet:
                        return packet

    async def send_command(self, payload: bytes) -> None:
        """Send configuration JSON with bounded ACK/NAK retries."""
        try:
            command = json.loads(payload)
        except (ValueError, UnicodeError) as exc:
            raise Evb034ProtocolError("Expected configuration JSON") from exc
        if not isinstance(command, dict) or not command:
            raise Evb034ProtocolError("Expected configuration object")
        async with self._operation_lock:
            chunks = frame_payload(payload, self.write_size)
            index = attempts = 0
            self._command_active = True
            try:
                while index < len(chunks):
                    self._acks.clear()
                    after = self._notification_serial
                    await self._send(chunks[index])
                    # restartTimeout's timer reports failure; it does not emit
                    # a timeout query or invent a retransmission on silence.
                    async with asyncio.timeout(TIMEOUT):
                        while True:
                            serial, response = await self._wait_ready(self._acks, TIMEOUT)
                            if serial > after:
                                break
                    if response in (b"\xc2", b"\xc3"):
                        if self._timeout_retries > PACKET_ATTEMPTS:
                            raise Evb034ProtocolError("Timeout retries exceeded")
                        # resetCurrentEntry clears processing state, causing
                        # startProcessor to re-chunk the original queue entry.
                        index = attempts = 0
                        continue
                    if response == ACK:
                        index += 1
                        attempts = 0
                    else:
                        attempts += 1
                        if attempts >= PACKET_ATTEMPTS:
                            raise Evb034ProtocolError("Packet retries exceeded")
            finally:
                self._command_active = False
                self._acks.clear()

    async def next_packet(self, *, after: int = 0) -> dict:
        """Wait a bounded time for a complete update from this client."""
        async with asyncio.timeout(TIMEOUT * 3):
            while True:
                serial, packet = await self._wait_ready(self._packets, TIMEOUT * 3)
                if serial > after:
                    return packet

    def pending_packets(self) -> list[dict]:
        """Consume received deltas without transmitting a read request."""
        self.check_health()
        packets = [packet for _, packet in self._packets]
        self._packets.clear()
        return packets

    def _publish_packet(self, packet: dict, serial: int, received_at: float) -> None:
        # CsBlePacket.updatePacket skips JsonNull before interpreting any key.
        # Filter before access validation and coalescing, not only at HA refresh.
        packet = {key: value for key, value in packet.items() if value is not None}
        if not packet:
            return
        received = ReceivedPacket()
        received.update(packet)
        received.confirmed_at = {key: received_at for key in packet}
        packet = received
        if self._authenticated and "as" in packet and (
            type(packet["as"]) is not int or packet["as"] != 2
        ):
            raise Evb034ProtocolError("Authenticated session lost access")
        if self._authenticated and self._on_packet is not None:
            # Receipt and publication have different schedules. Observe live
            # packets now without letting publication manufacture a timestamp.
            self._on_packet(packet)
        # Ordinary deltas coalesce while HA waits for its configured interval.
        # Keep authentication transitions ordered instead of overwriting them.
        if self._packets and "as" not in packet and "as" not in self._packets[-1][1]:
            previous = self._packets[-1][1]
            previous.update(packet)
            self._packets[-1] = serial, previous
            if len(json.dumps(previous)) > MAX_PACKET_BYTES * 4:
                raise Evb034ProtocolError("Pending update size exceeded")
        else:
            if len(self._packets) >= 128:
                raise Evb034ProtocolError("Pending update count exceeded")
            self._packets.append((serial, packet))
        self._ready.set()

    async def _receive_loop(self) -> None:
        try:
            while not self._closed and self._failure is None:
                if self._buffer is not None:
                    async with asyncio.timeout(TIMEOUT):
                        serial, data, received_at = await self._queue.get()
                else:
                    serial, data, received_at = await self._queue.get()
                await self._process_notification(data, serial, received_at)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._fail(exc)

    async def _process_notification(self, data: bytes, serial: int, received_at: float) -> None:
        if not data:
            raise Evb034ProtocolError("Empty notification")
        if len(data) == 1:
            if data in (ACK, NAK):
                if len(self._acks) >= 16:
                    raise Evb034ProtocolError("Status queue full")
                self._acks.append((serial, data))
                self._ready.set()
                return
            if data in (b"\xe0", b"\xf0"):
                await self._send(b"\xf0" if data == b"\xe0" else b"\xe0")
                return
            if data in (b"\xc2", b"\xc3"):
                # processStatusPacket replies in both directions and retains
                # its timeout count across queue entries in this processor.
                await self._send(b"\xc3" if data == b"\xc2" else b"\xc2")
                self._timeout_retries += 1
                if self._command_active:
                    self._acks.clear()
                    self._acks.append((serial, data))
                    self._ready.set()
                return
        # processDataPacket validates each complete ATT notification, including
        # a short final chunk. Invalid bytes never spill into the next frame.
        if (not 3 <= len(data) <= 512 or crc_hqx(data[:-2], 0xFFFF)
                != int.from_bytes(data[-2:], "little")):
            await self._send(NAK)
            return
        header = data[0]
        if header not in (0, 0x40, 0x80, 0xC0):
            raise Evb034ProtocolError("Invalid data flags")
        if header & 0x80:
            # A first marker restarts assembly, including retransmissions.
            self._buffer = bytearray()
        if self._buffer is None:
            await self._send(NAK)
            raise Evb034ProtocolError("Chunk without first marker")
        self._buffer.extend(data[1:-2])
        if len(self._buffer) > MAX_PACKET_BYTES:
            raise Evb034ProtocolError("Packet assembly limit exceeded")
        if header & 0x40:
            raw = bytes(self._buffer)
            self._buffer = None
            try:
                packet = json.loads(raw, object_pairs_hook=_unique_object,
                                    parse_constant=_reject_constant)
            except (ValueError, UnicodeError) as exc:
                raise Evb034ProtocolError("Invalid JSON packet") from exc
            if not isinstance(packet, dict) or not packet:
                raise Evb034ProtocolError("Expected nonempty JSON object")
            self._publish_packet(packet, serial, received_at)
        await self._send(ACK)
