"""C6.x notifications must progress without invented read requests."""

import asyncio

from homeassistant.helpers import entity_registry as er
from custom_components.chandler_legacy_view.const import DOMAIN
from evb034_packets import frames
from test_evb034_authentication import setup_valve
from test_evb034_entities import sensor_id


async def test_long_publication_interval_keeps_acknowledging_the_live_peer(
    hass, runtime_factory, bluetooth_io, transport, clock,
):
    entry, connection, valve, _ = await setup_valve(hass, runtime_factory, bluetooth_io, transport)
    await connection.async_set_persistent_poll_interval(300)
    await connection.async_set_persistent_connection_enabled(True)
    await clock.run(connection.async_poll())
    owner = valve.clients[0]
    usage = sensor_id(hass, "water_usage_today")
    assert float(hass.states.get(usage).state) == 5.67
    initial_success = connection.last_success
    valve.dashboards.append(frames({"dwu": 78900}))
    await clock.until(lambda: valve.dashboard_notifications >= 12, budget=20)
    # The peer requires an ACK within three seconds for every ordinary frame.
    # A receiver tied to the 300-second HA interval would have disconnected.
    assert owner.is_connected
    assert owner.disconnects == 0
    assert float(hass.states.get(usage).state) == 5.67
    assert connection.last_success == initial_success
    assert valve.packet_requests == []
    assert all(payload not in (b"t" * 20, b"u" * 20) for _, payload in valve.writes)
    valve.stream_enabled = False
    await clock.until(lambda: float(hass.states.get(usage).state) == 789, budget=310)
    assert connection.last_success > initial_success
    await clock.run(hass.config_entries.async_unload(entry.entry_id))
    assert not owner.is_connected
    assert owner.notifications == {}
    assert all(task.done() for task in owner.peer_tasks)


async def test_receiver_failure_releases_simulated_owner_before_long_interval(
    hass, runtime_factory, bluetooth_io, transport, clock,
):
    _, connection, valve, _ = await setup_valve(hass, runtime_factory, bluetooth_io, transport)
    await connection.async_set_persistent_poll_interval(300)
    await connection.async_set_persistent_connection_enabled(True)
    await clock.run(connection.async_poll())
    owner = valve.clients[0]
    callback = owner.notification_history[0]
    for _ in range(140):
        callback(None, bytearray(b"\xcc"))
    await clock.until(lambda: not owner.is_connected, budget=10)
    assert owner.disconnects == 1
    assert owner.notifications == {}
    assert all(task.done() for task in owner.peer_tasks)
    assert not connection.authentication_lockout
    await clock.run(connection.async_set_persistent_connection_enabled(False))


async def test_duplicate_initial_auth_state_is_not_a_response_to_the_pin_write(
    hass, runtime_factory, bluetooth_io, transport, clock,
):
    _, connection, valve, lockout = await setup_valve(hass, runtime_factory, bluetooth_io, transport)
    initial = frames({"dlf": 622, "dlc": 23, "as": 1, "gbre": 1})
    # Both messages were delivered before the PIN write. The second queued
    # state must not be counted as a credential rejection after that write.
    gate = valve.operations["identify"].block()
    valve.identification_packets.append([])
    poll = asyncio.create_task(connection.async_poll())
    await clock.run(gate.started.wait())
    for frame in initial + frames({"as": 1}):
        valve.clients[0].notify(frame)
    gate.release.set()
    await clock.run(poll)
    assert connection.last_success is not None
    assert len(valve.authentication_payloads) == 1
    assert hass.states.get(lockout).state == "off"


async def test_unload_waits_for_cancellation_during_a_streamed_initial_read(
    hass, runtime_factory, bluetooth_io, transport, clock,
):
    entry, connection, valve, _ = await setup_valve(hass, runtime_factory, bluetooth_io, transport)
    gate = valve.operations["dashboard"].block()
    poll = asyncio.create_task(connection.async_poll())
    await clock.run(gate.started.wait())
    owner = valve.clients[0]
    callback = owner.notification_history[0]
    unload = asyncio.create_task(hass.config_entries.async_unload(entry.entry_id))
    poll.cancel()
    try:
        await clock.run(poll)
    except asyncio.CancelledError:
        pass
    await clock.run(unload)
    assert gate.cancelled
    assert owner.disconnects == 1
    assert all(task.done() for task in owner.peer_tasks)
    assert owner.notifications == {}
    identifier = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, "valve-a_water_usage_today")
    unloaded_state = hass.states.get(identifier)
    assert unloaded_state is None or unloaded_state.state == "unavailable"
    for frame in frames({"dwu": 99900}):
        callback(None, bytearray(frame))
    await hass.async_block_till_done()
    assert hass.states.get(identifier) is unloaded_state


async def test_blocked_authentication_write_times_out_without_credential_lockout(
    hass, runtime_factory, bluetooth_io, transport, clock,
):
    _, connection, valve, lockout = await setup_valve(hass, runtime_factory, bluetooth_io, transport)
    gate = valve.operations["authenticate"].block()
    await clock.run(connection.async_poll(), budget=15)
    assert gate.started.is_set()
    assert gate.cancelled
    assert connection.last_success is None
    assert hass.states.get(lockout).state == "off"
    assert not valve.clients[0].is_connected
    assert all(task.done() for task in valve.clients[0].peer_tasks)
