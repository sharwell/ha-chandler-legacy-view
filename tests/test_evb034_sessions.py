"""Exercise production C6.x sessions at the shared external GATT boundary."""

import asyncio
import pytest

from packets import EVB034_622
from evb034_packets import frames


def newer_connection(connection_factory, bluetooth_io, transport, mtu=23):
    bluetooth_io.advertise(payload=EVB034_622)
    valve = transport.valve()
    valve.firmware_version = 622
    valve.mtu_size = mtu
    return connection_factory(), valve


@pytest.mark.parametrize("mtu", [23, 64, 247, 517])
async def test_identifies_newer_valves_with_negotiated_packet_sizes(
    connection_factory, bluetooth_io, transport, clock, mtu,
):
    connection, valve = newer_connection(connection_factory, bluetooth_io, transport, mtu)
    await clock.run(connection.async_poll())
    assert connection.evb034_packet["dlf"] == 622
    assert connection.evb034_packet["dlc"] == 23
    assert valve.packet_requests == []
    assert all(payload not in (b"t" * 20, b"u" * 20) for _, payload in valve.writes)
    assert valve.authentication_payloads[0] in [payload for _, payload in valve.writes]
    assert connection.last_success is not None
    assert valve.clients[0].notifications == {}
    assert not valve.clients[0].is_connected


async def test_valid_notification_with_crc_matching_prefix_can_authenticate(
    connection_factory, bluetooth_io, transport, clock,
):
    connection, valve = newer_connection(connection_factory, bluetooth_io, transport, mtu=64)
    valve.connection_counter = 5131
    # This complete notification also contains a shorter CRC-valid prefix.
    # Searching for that prefix truncates valid JSON and prevents all readings.
    valve.identification_packets.append(frames({"dlf": 622, "dlc": 5131, "as": 1}, 61))
    await clock.run(connection.async_poll())
    assert connection.last_success is not None
    assert connection.evb034_packet["dlc"] == 5131
    assert connection.evb034_packet["dwu"] == 567
    assert not connection.authentication_lockout


@pytest.mark.parametrize("delivery", ["fragmented", "coalesced", "keepalive"])
async def test_notification_transport_boundaries_preserve_identification(
    connection_factory, bluetooth_io, transport, clock, delivery,
):
    connection, valve = newer_connection(connection_factory, bluetooth_io, transport)
    reply = frames({"dlf": 622, "dlc": 91})
    if delivery == "fragmented":
        reply = [part for frame in reply for part in (frame[:4], frame[4:])]
    elif delivery == "coalesced":
        reply = [b"".join(reply)]
    else:
        reply = [b"\xe0", *reply, b"\xc2"]
    valve.identification_packets.append(reply)
    await clock.run(connection.async_poll())
    assert connection.evb034_packet == {"dlf": 622, "dlc": 91}
    if delivery == "keepalive":
        assert b"\xf0" in [payload for _, payload in valve.writes]


@pytest.mark.parametrize("damage", ["crc", "json", "missing", "duplicate", "stale", "wrong-family", "empty"])
async def test_invalid_or_incomplete_packets_cannot_establish_identification(
    connection_factory, bluetooth_io, transport, clock, damage,
):
    connection, valve = newer_connection(connection_factory, bluetooth_io, transport)
    reply = frames({"dlf": 622, "dlc": 91, "padding": "distinguishable"})
    if damage == "crc":
        reply[0] = reply[0][:-1] + bytes((reply[0][-1] ^ 1,))
    elif damage == "json":
        reply = frames(b'{"dlf":622,broken}')
    elif damage == "missing":
        reply = reply[:-1]
    elif damage == "duplicate":
        reply.insert(1, reply[0])
    elif damage == "stale":
        reply = reply[1:]
    elif damage == "wrong-family":
        reply = frames({"dlf": 422})
    else:
        reply = frames(b"{}")
    valve.identification_packets.append(reply)
    await clock.run(connection.async_poll())
    assert connection.evb034_packet is None
    assert connection.last_success is None
    assert not valve.clients[0].is_connected


@pytest.mark.parametrize("replies,successful", [([b"\xc8", b"\xcc"], True), ([b"\xc8"] * 2, False)])
async def test_negative_acknowledgements_retry_only_within_the_packet_budget(
    connection_factory, bluetooth_io, transport, clock, replies, successful,
):
    connection, valve = newer_connection(connection_factory, bluetooth_io, transport)
    valve.identification_packets.append(frames({
        "dlf": 622, "dlc": 23, "as": 1, "dlic": 0, "gbre": 0,
    }))
    valve.chunk_acknowledgements.extend(replies)
    await clock.run(connection.async_poll())
    assert (connection.last_success is not None) == successful
    chunks = [payload for _, payload in valve.writes if payload[:2] == b"\xc0{"]
    assert chunks[0] == chunks[1]
    if not successful:
        assert len(chunks) == 2


async def test_cancelled_session_cannot_lend_identification_to_replacement(
    connection_factory, bluetooth_io, transport, clock,
):
    connection, valve = newer_connection(connection_factory, bluetooth_io, transport)
    gate = valve.operations["device_list"].block()
    poll = asyncio.create_task(connection.async_poll())
    await clock.run(gate.started.wait())
    callback = valve.clients[0].notification_history[0]
    poll.cancel()
    with pytest.raises(asyncio.CancelledError):
        await clock.run(poll)
    assert connection.evb034_packet is None
    gate.release.set()
    await clock.advance(61)
    for frame in frames({"dlf": 622, "dlc": 999}):
        callback(None, bytearray(frame))
    await clock.run(connection.async_poll())
    assert connection.evb034_packet["dlc"] == 24
    assert all(not client.is_connected for client in valve.clients)


async def test_crc_failure_requests_retransmission_before_accepting_packet(
    connection_factory, bluetooth_io, transport, clock,
):
    connection, valve = newer_connection(connection_factory, bluetooth_io, transport)
    reply = frames({"dlf": 622, "dlc": 67})
    damaged = reply[0][:-1] + bytes((reply[0][-1] ^ 1,))
    valve.identification_packets.append([damaged, *reply])
    await clock.run(connection.async_poll())
    assert connection.evb034_packet == {"dlf": 622, "dlc": 67}
    assert b"\xc8" in [payload for _, payload in valve.writes]


@pytest.mark.parametrize("failure", ["missing-ack", "id-nak"])
async def test_missing_or_rejected_acknowledgement_has_a_bounded_failure(
    connection_factory, bluetooth_io, transport, clock, failure,
):
    connection, valve = newer_connection(connection_factory, bluetooth_io, transport)
    if failure == "missing-ack":
        valve.identification_packets.append(frames({
            "dlf": 622, "dlc": 23, "as": 1, "dlic": 0, "gbre": 0,
        }))
        valve.chunk_acknowledgements.extend([b""] * 2)
    else:
        valve.id_acknowledgements.extend([b"\xc8"] * 2)
    await clock.run(connection.async_poll(), budget=30)
    assert connection.last_success is None
    assert not valve.clients[0].is_connected
    assert len(valve.writes) < 20
