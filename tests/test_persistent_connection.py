"""Session ownership through public connection APIs and simulated GATT I/O."""

import asyncio
import pytest
from packets import dashboard_frames


@pytest.mark.parametrize("started", [False, True], ids=["queued-handoff", "running-session"])
@pytest.mark.parametrize("stop", ["disable", "unload"])
async def test_stop_disconnects_owned_session_once(connection_factory, transport, clock, started, stop):
    events = []
    connection = connection_factory(state_callback=lambda address, connected: events.append((address, connected)))
    await connection.async_set_persistent_connection_enabled(True)
    await connection.async_poll()
    valve = transport.valve()
    if started:
        await clock.drain()
    assert valve.clients[0].is_connected
    assert events == [("valve-a", True)]
    operation = connection.async_unload() if stop == "unload" else connection.async_set_persistent_connection_enabled(False)
    await clock.run(operation)
    assert valve.clients[0].disconnects == 1
    assert not valve.clients[0].is_connected
    assert events == [("valve-a", True), ("valve-a", False)]


async def test_one_shot_poll_publishes_connected_then_disconnected(connection_factory, transport, clock):
    events = []
    connection = connection_factory(state_callback=lambda address, connected: events.append(connected))
    await clock.run(connection.async_poll())
    assert events == [True, False]
    assert transport.valve().clients[0].disconnects == 1
    assert not transport.valve().clients[0].is_connected


async def test_concurrent_stops_before_handoff_share_disconnect(connection_factory, transport, clock):
    connection = connection_factory()
    await connection.async_set_persistent_connection_enabled(True)
    disable = asyncio.create_task(connection.async_set_persistent_connection_enabled(False))
    unload = asyncio.create_task(connection.async_unload())
    await connection.async_poll()
    await clock.run(asyncio.gather(disable, unload))
    assert transport.valve().clients[0].disconnects == 1
    assert not transport.valve().clients[0].is_connected


@pytest.mark.parametrize("started", [False, True])
async def test_poll_cannot_reconnect_while_stop_owns_cleanup(connection_factory, transport, clock, started):
    connection = connection_factory()
    await connection.async_set_persistent_connection_enabled(True)
    valve = transport.valve()
    gate = valve.operations["disconnect"].block()
    stop = None
    try:
        if started:
            await connection.async_poll()
            await clock.drain()
            stop = asyncio.create_task(connection.async_set_persistent_connection_enabled(False))
        else:
            stop = asyncio.create_task(connection.async_set_persistent_connection_enabled(False))
            await connection.async_poll()
        await clock.run(gate.started.wait())
        assert not stop.done()
        # Let the genuine retry cooldown expire while cleanup is still gated.
        # The independent disconnect deadline is thirty seconds after RESET.
        await clock.advance(29.95)
        await connection.async_poll()
        assert transport.attempts == ["valve-a"]
        assert valve.clients[0].is_connected
        assert not stop.done()
    finally:
        gate.release.set()
        if stop is not None:
            await clock.run(stop)
    await clock.advance(1)
    await clock.run(connection.async_poll())
    assert len(valve.clients) == 2
    assert valve.clients[0] is not valve.clients[1]
    assert all(not client.is_connected for client in valve.clients)


@pytest.mark.parametrize("started", [False, True])
async def test_reenable_during_cleanup_retries_after_release(hass, connection_factory, transport, clock, started):
    connection = connection_factory()
    await connection.async_set_persistent_connection_enabled(True)
    valve = transport.valve()
    valve.dashboards.extend([dashboard_frames(gallons=123), dashboard_frames(gallons=987)])
    gate = valve.operations["disconnect"].block()
    stop = None
    try:
        if started:
            await connection.async_poll()
            await clock.drain()
            stop = asyncio.create_task(connection.async_set_persistent_connection_enabled(False))
        else:
            stop = asyncio.create_task(connection.async_set_persistent_connection_enabled(False))
            await connection.async_poll()
        await clock.run(gate.started.wait())
        await hass.async_start()
        await connection.async_set_persistent_connection_enabled(True)
        await clock.drain()
        assert connection.dashboard_data.water_usage == 123
        assert transport.attempts == ["valve-a"]
        assert not stop.done()
        gate.release.set()
        await clock.run(stop)
        await clock.until(lambda: len(valve.clients) == 2
                          and connection.dashboard_data.water_usage == 987)
        assert len(valve.clients) == 2
        assert not valve.clients[0].is_connected
        assert valve.clients[1].is_connected
        assert connection.dashboard_data.water_usage == 987
    finally:
        gate.release.set()
        if stop is not None:
            await clock.run(stop)
        await clock.run(connection.async_set_persistent_connection_enabled(False))
    assert [client.disconnects for client in valve.clients] == [1, 1]


async def test_disable_interrupts_active_dashboard_then_disconnects(connection_factory, transport, clock):
    connection = connection_factory()
    await connection.async_set_persistent_connection_enabled(True)
    await connection.async_poll()
    valve = transport.valve()
    gate = valve.operations["dashboard"].block()
    gate.started.clear()
    await clock.advance(10.1)
    assert gate.started.is_set()
    await clock.run(connection.async_set_persistent_connection_enabled(False))
    assert gate.cancelled
    assert not valve.clients[0].is_connected
    assert valve.clients[0].notifications == {}
    assert valve.clients[0].disconnects == 1


async def test_normal_keepalive_failure_disconnects_once(connection_factory, transport, clock):
    connection = connection_factory()
    await connection.async_set_persistent_connection_enabled(True)
    await connection.async_poll()
    valve = transport.valve()
    valve.operations["dashboard"].failure = OSError("simulated link lost")
    await clock.advance(10.1)
    await clock.advance(0.11)
    await clock.run(connection.async_unload())
    assert valve.clients[0].disconnects == 1
    assert not valve.clients[0].is_connected


async def test_old_notifications_cannot_satisfy_replacement_request(connection_factory, transport, clock):
    connection = connection_factory()
    valve = transport.valve()
    valve.dashboards.append(dashboard_frames(hour=4, minute=12, gallons=111))
    await clock.run(connection.async_poll())
    old_handler = valve.clients[0].notification_history[-1]
    assert connection.dashboard_data.water_usage == 111
    await clock.advance(31)
    valve.dashboards.append([])
    poll = asyncio.create_task(connection.async_poll())
    try:
        await clock.drain()
        assert len(valve.clients) == 2
        assert valve.clients[1].notifications
        for packet in dashboard_frames(gallons=222):
            old_handler("old-notify", bytearray(packet))
        await clock.drain()
        assert not poll.done()
        assert connection.dashboard_data.water_usage == 111
        for packet in dashboard_frames(hour=9, minute=34, gallons=999):
            valve.clients[1].notify(packet)
        await clock.run(poll)
        data = connection.dashboard_data
        assert (data.time_hour, data.time_minute, data.water_usage) == (9, 34, 999)
    finally:
        if not poll.done():
            poll.cancel()
            await clock.run(asyncio.gather(poll, return_exceptions=True))
    assert all(not client.is_connected for client in valve.clients)
