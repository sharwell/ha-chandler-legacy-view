"""Reference timeout status replies and command restart through simulated GATT."""

import pytest

from custom_components.chandler_legacy_view.evb034 import Evb034ProtocolError, Evb034Session
from ble import NOTIFY, WRITE
from evb034_packets import frames
from packets import EVB034_622
from test_evb034_authentication import setup_valve
from test_evb034_sessions import newer_connection


@pytest.mark.parametrize("status,response", [(b"\xc2", b"\xc3"), (b"\xc3", b"\xc2")])
async def test_idle_timeout_status_gets_the_reference_reply_without_disconnect(
    hass, runtime_factory, bluetooth_io, transport, clock, status, response,
):
    _, connection, valve, _ = await setup_valve(hass, runtime_factory, bluetooth_io, transport)
    await connection.async_set_persistent_connection_enabled(True)
    await clock.run(connection.async_poll())
    valve.stream_enabled = False
    owner = valve.clients[0]
    before = len(valve.writes)
    owner.notify(status)
    await clock.drain()
    assert [payload for _, payload in valve.writes[before:]] == [response]
    assert owner.is_connected
    await clock.run(connection.async_set_persistent_connection_enabled(False))


async def test_scripted_timeout_exchange_stops_when_the_peer_stops_sending_statuses(
    hass, runtime_factory, bluetooth_io, transport, clock,
):
    _, connection, valve, _ = await setup_valve(hass, runtime_factory, bluetooth_io, transport)
    await connection.async_set_persistent_connection_enabled(True)
    await clock.run(connection.async_poll())
    valve.stream_enabled = False
    owner = valve.clients[0]
    valve.timeout_responses.append(b"\xc3")
    before = len(valve.writes)
    owner.notify(b"\xc2")
    await clock.advance(connection.persistent_poll_interval + 1)
    assert [payload for _, payload in valve.writes[before:]] == [b"\xc3", b"\xc2"]
    assert owner.is_connected
    assert not valve.timeout_responses
    await clock.run(connection.async_set_persistent_connection_enabled(False))


@pytest.fixture
async def packet_session(bluetooth_io, transport, clock):
    """Production packet session using the shared external GATT peer."""
    observation = bluetooth_io.advertise(payload=EVB034_622)
    valve = transport.valve()
    valve.firmware_version = 622
    valve.authentication_required = False
    valve.stream_enabled = False
    client = await transport.connect(None, observation.device, "valve")

    async def write(payload):
        await client.write_gatt_char(WRITE, payload, response=True)

    session = Evb034Session(write, 20)
    await client.start_notify(NOTIFY, session.notification)
    try:
        await clock.run(session.identify())
        session.mark_authenticated()
        yield session, client, valve
    finally:
        await session.close()
        await client.stop_notify(NOTIFY)
        await clock.run(client.disconnect())
    assert all(task.done() for task in client.peer_tasks)


@pytest.mark.parametrize("status,response", [(b"\xc2", b"\xc3"), (b"\xc3", b"\xc2")])
async def test_timeout_status_restarts_the_whole_command_after_an_acked_chunk(
    packet_session, clock, status, response,
):
    session, client, valve = packet_session
    payload = b'{"dwh":20,"ddo":5,"gbre":1}'
    chunks = frames(payload)
    assert len(chunks) == 2
    valve.chunk_acknowledgements.extend([b"\xcc", status, b"\xcc", b"\xcc"])
    before = len(valve.writes)
    await clock.run(session.send_command(payload))
    writes = [value for _, value in valve.writes[before:]]
    # resetCurrentEntry re-chunks the original queue entry; retrying only
    # the interrupted last chunk can apply an incomplete configuration.
    assert [value for value in writes if len(value) > 1] == chunks + chunks
    assert response in writes
    assert valve.packet_requests == [payload]
    assert client.is_connected


async def test_timeout_budget_persists_across_commands_and_drops_only_failed_entry(
    packet_session, clock,
):
    session, client, valve = packet_session
    command = b'{"gbre":1}'
    for _ in range(2):
        valve.chunk_acknowledgements.extend([b"\xc3", b"\xcc"])
        await clock.run(session.send_command(command))
    valve.chunk_acknowledgements.append(b"\xc3")
    with pytest.raises(Evb034ProtocolError, match="Timeout retries exceeded"):
        await clock.run(session.send_command(command))
    assert valve.packet_requests == [command, command]
    # The failed queue entry is dropped; a later command with an ordinary ACK
    # can still complete without inheriting an interrupted partial message.
    await clock.run(session.send_command(command))
    assert valve.packet_requests == [command, command, command]
    assert client.is_connected


async def test_incomplete_receive_deadline_fails_without_emitting_timeout_queries(
    packet_session, clock,
):
    session, client, valve = packet_session
    before = len(valve.writes)
    client.notify(frames({"dwu": 12345, "gpf": 12345})[0])
    with pytest.raises(Evb034ProtocolError, match="Notification receiver failed"):
        await clock.run(session.next_packet(), budget=5)
    assert [value for _, value in valve.writes[before:]] == [b"\xcc"]


@pytest.mark.parametrize("statuses,successful", [
    ([b"\xc3"], True), ([b"\xc2", b"\xc3"], True),
    ([b"\xc2", b"\xc3", b"\xc2"], False),
])
async def test_timeout_recovery_obeys_the_reference_budget_and_drains_the_owner(
    connection_factory, bluetooth_io, transport, clock, statuses, successful,
):
    connection, valve = newer_connection(connection_factory, bluetooth_io, transport)
    valve.identification_packets.append(frames({
        "dlf": 622, "dlc": 23, "as": 1, "dlic": 0, "gbre": 0,
    }))
    valve.chunk_acknowledgements.extend([*statuses, b"\xcc"])
    await clock.run(connection.async_poll(), budget=20)
    assert (connection.last_success is not None) == successful
    assert not connection.authentication_lockout
    owner = valve.clients[0]
    assert not owner.is_connected
    assert owner.notifications == {}
    assert all(task.done() for task in owner.peer_tasks)
    writes = [payload for _, payload in valve.writes]
    assert writes.count(b"\xc3") == statuses.count(b"\xc2")
    assert writes.count(b"\xc2") == statuses.count(b"\xc3")
    assert sum(payload[:2] == b"\xc0{" for payload in writes) == min(len(statuses) + 1, 3)


async def test_silent_command_deadline_fails_without_inventing_a_timeout_query(
    connection_factory, bluetooth_io, transport, clock,
):
    connection, valve = newer_connection(connection_factory, bluetooth_io, transport)
    valve.identification_packets.append(frames({
        "dlf": 622, "dlc": 23, "as": 1, "dlic": 0, "gbre": 0,
    }))
    valve.chunk_acknowledgements.extend([b"", b""])
    await clock.run(connection.async_poll(), budget=10)
    assert connection.last_success is None
    assert b"\xc2" not in [payload for _, payload in valve.writes]
    assert not valve.clients[0].is_connected


async def test_unload_cancels_a_blocked_timeout_reply_and_invalidates_callbacks(
    hass, runtime_factory, bluetooth_io, transport, clock,
):
    entry, connection, valve, _ = await setup_valve(hass, runtime_factory, bluetooth_io, transport)
    await connection.async_set_persistent_connection_enabled(True)
    await clock.run(connection.async_poll())
    valve.stream_enabled = False
    owner = valve.clients[0]
    callback = owner.notification_history[0]
    gate = valve.operations["timeout"].block()
    owner.notify(b"\xc3")
    await clock.run(gate.started.wait(), budget=5)
    await clock.run(hass.config_entries.async_unload(entry.entry_id))
    assert gate.cancelled
    assert not owner.is_connected
    assert owner.notifications == {}
    assert all(task.done() for task in owner.peer_tasks)
    writes = len(valve.writes)
    callback(None, bytearray(b"\xc3"))
    await clock.drain()
    assert len(valve.writes) == writes
