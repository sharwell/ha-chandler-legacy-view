"""Connection-owned Evb034 wire exchange from Water System 6.18.2.

See CsBleQueueProcessor.chunkPacket/processResponse/processDataPacket and
CsBleLeManager.configureConnection$lambda$7 (ATT MTU minus three bytes).
"""

from __future__ import annotations

import asyncio
from binascii import crc_hqx
import json
from collections.abc import Awaitable, Callable

ACK = b"\xcc"
NAK = b"\xc8"
ID = b"\xea"
TIMEOUT = 3.0
MAX_PACKET_BYTES = 2048
PACKET_ATTEMPTS = 2


class Evb034ProtocolError(ValueError):
    """An exchange cannot safely produce a packet update."""


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


class Evb034Session:
    """Serialize exchanges and keep receive state with the owning GATT client.

    Notification callbacks only enqueue bytes. The exchange coroutine owns all
    ACK/NAK writes and assembly, so teardown needs no detached worker tasks.
    """

    def __init__(self, write: Callable[[bytes], Awaitable[None]], size: int) -> None:
        self.write_size = size
        self._write = write
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=128)
        self._lock = asyncio.Lock()
        self._closed = False
        self._overflow = False
        self._buffer: bytearray | None = None
        self._wire = bytearray()

    def notification(self, _sender, data: bytearray) -> None:
        """Ignore notifications after this client has relinquished ownership."""
        if self._closed:
            return
        try:
            self._queue.put_nowait(bytes(data))
        except asyncio.QueueFull:
            self._overflow = True

    def close(self) -> None:
        """Invalidate callbacks and discard all pending session evidence."""
        self._closed = True
        self._reset_receive()

    def _reset_receive(self) -> None:
        self._buffer = None
        self._wire.clear()
        while not self._queue.empty():
            self._queue.get_nowait()

    async def identify(self) -> dict:
        """Select the packet protocol, then request fresh identification."""
        async with self._lock:
            self._reset_receive()
            for attempt in range(PACKET_ATTEMPTS):
                await self._write(ID)
                try:
                    event = await self._next_event()
                except TimeoutError:
                    if attempt + 1 == PACKET_ATTEMPTS:
                        raise
                    continue
                if event == ACK:
                    break
            else:
                raise Evb034ProtocolError("Identification was not acknowledged")
            packet = await self._request(b"t" * 20)
            firmware = packet.get("dlf")
            if type(firmware) is not int or not 600 <= firmware <= 9999:
                raise Evb034ProtocolError("Invalid Evb034 identification")
            return packet

    async def request(self, payload: bytes) -> dict:
        """Return only a complete JSON response to this serialized request."""
        async with self._lock:
            self._reset_receive()
            return await self._request(payload)

    async def _request(self, payload: bytes) -> dict:
        chunks = frame_payload(payload, self.write_size)
        for index, chunk in enumerate(chunks):
            for attempt in range(PACKET_ATTEMPTS):
                await self._write(chunk)
                event = await self._next_event()
                if isinstance(event, dict):
                    if index != len(chunks) - 1:
                        raise Evb034ProtocolError("Response before request completed")
                    return event
                if event == ACK:
                    break
                if event != NAK or attempt + 1 == PACKET_ATTEMPTS:
                    raise Evb034ProtocolError("Packet retries exceeded")
            else:
                raise Evb034ProtocolError("Packet not acknowledged")
        while True:
            event = await self._next_event()
            if isinstance(event, dict):
                return event
            if event == NAK:
                raise Evb034ProtocolError("Response rejected")

    async def _next_event(self) -> bytes | dict:
        # Status traffic must not extend the operation's overall deadline.
        async with asyncio.timeout(TIMEOUT * 3):
            for timeout_attempt in range(3):
                try:
                    async with asyncio.timeout(TIMEOUT):
                        while True:
                            if self._closed or self._overflow:
                                raise Evb034ProtocolError("Session closed or receive queue full")
                            event = await self._read_notification()
                            if event is not None:
                                return event
                except TimeoutError:
                    if timeout_attempt == 2:
                        raise
                    await self._write(b"\xc2")
        raise TimeoutError

    async def _read_notification(self) -> bytes | dict | None:
        data = await self._queue.get()
        if not data:
            raise Evb034ProtocolError("Empty notification")
        if not self._wire and len(data) == 1:
            if data in (ACK, NAK):
                return data
            if data in (b"\xe0", b"\xf0"):
                await self._write(b"\xf0" if data == b"\xe0" else b"\xe0")
                return None
            if data in (b"\xc2", b"\xc3"):
                if data == b"\xc2":
                    await self._write(b"\xc3")
                return None
        self._wire.extend(data)
        if len(self._wire) > MAX_PACKET_BYTES:
            raise Evb034ProtocolError("Receive limit exceeded")
        # Notifications normally contain one complete frame. CRC boundaries also
        # permit transport fragmentation and multiple frames in one callback.
        while len(self._wire) >= 3:
            end = next((end for end in range(3, min(len(self._wire), 512) + 1)
                        if crc_hqx(self._wire[:end - 2], 0xFFFF)
                        == int.from_bytes(self._wire[end - 2:end], "little")), None)
            if end is None:
                if len(self._wire) >= self.write_size:
                    self._wire.clear()
                    await self._write(NAK)
                return None
            frame = bytes(self._wire[:end])
            del self._wire[:end]
            header = frame[0]
            if header not in (0, 0x40, 0x80, 0xC0):
                raise Evb034ProtocolError("Invalid data flags")
            if header & 0x80:
                if self._buffer is not None:
                    raise Evb034ProtocolError("Overlapping first chunks")
                self._buffer = bytearray()
            if self._buffer is None:
                await self._write(NAK)
                raise Evb034ProtocolError("Chunk without first marker")
            self._buffer.extend(frame[1:-2])
            if len(self._buffer) > MAX_PACKET_BYTES:
                raise Evb034ProtocolError("Packet assembly limit exceeded")
            await self._write(ACK)
            if header & 0x40:
                raw = bytes(self._buffer)
                self._buffer = None
                try:
                    packet = json.loads(raw, object_pairs_hook=_unique_object,
                                        parse_constant=lambda _: None)
                except (ValueError, UnicodeError) as exc:
                    raise Evb034ProtocolError("Invalid JSON packet") from exc
                if not isinstance(packet, dict) or not packet:
                    raise Evb034ProtocolError("Expected nonempty JSON object")
                return packet
        return None
