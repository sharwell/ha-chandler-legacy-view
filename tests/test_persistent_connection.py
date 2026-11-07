"""Session ownership through public connection APIs and simulated GATT I/O."""

import asyncio
import pytest
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from custom_components.chandler_legacy_view.const import DOMAIN
from ble import NOTIFY
from packets import dashboard_frames


async def test_persistent_refreshes_publish_new_readings_on_same_session(hass, runtime_factory, bluetooth_io, transport, clock):
    await runtime_factory()
    bluetooth_io.advertise()
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    usage = registry.async_get_entity_id("sensor", DOMAIN, "valve-a_water_usage_today")
    persistent = registry.async_get_entity_id("switch", DOMAIN, "valve-a_persistent_connection")
    assert usage is not None
    assert persistent is not None
    assert hass.states.get(usage).state == "unknown"
    valve = transport.valve()
    valve.dashboards.extend(dashboard_frames(gallons=reading) for reading in (123, 456, 789))
    published = []
    # Unmarked synchronous listeners run in HA's executor and can lag behind
    # the state checks below. Record the ordered events on the event loop.
    @callback
    def record_usage_change(event):
        if event.data["entity_id"] == usage and event.data["new_state"] is not None:
            published.append(event.data["new_state"].state)

    remove = hass.bus.async_listen(EVENT_STATE_CHANGED, record_usage_change)
    try:
        await hass.services.async_call("switch", "turn_on", {"entity_id": persistent}, blocking=True)
        await hass.async_start()
        await clock.until(lambda: hass.states.get(usage).state == "123")
        assert len(valve.clients) == 1
        session = valve.clients[0]
        assert session.is_connected
        # Disconnect clears subscriptions legitimately. Successive published
        # readings must prove request cleanup while this same session is live,
        # otherwise a one-shot disconnect could conceal a subscription leak.
        for reading in (456, 789):
            await clock.until(lambda: hass.states.get(usage).state == str(reading), budget=15)
            assert valve.clients == [session]
            assert session.is_connected
            assert session.disconnects == 0
            assert session.notifications == {}
        assert published == ["123", "456", "789"]
        assert hass.states.get(persistent).state == "on"
    finally:
        await clock.run(hass.services.async_call("switch", "turn_off", {"entity_id": persistent}, blocking=True))
        remove()
    assert valve.clients == [session]
    assert not session.is_connected
    assert session.disconnects == 1
    assert session.notifications == {}
    assert hass.states.get(persistent).state == "off"
    assert hass.states.get(usage).state == "789"


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
    await connection.async_poll()
    disable = asyncio.create_task(connection.async_set_persistent_connection_enabled(False))
    unload = asyncio.create_task(connection.async_unload())
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
            # Return at the handoff, before the queued keepalive starts. Raw
            # Bluetooth calls may yield before this handoff, so requesting
            # disable before polling does not reliably exercise that case.
            await connection.async_poll()
            stop = asyncio.create_task(connection.async_set_persistent_connection_enabled(False))
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
            await connection.async_poll()
            stop = asyncio.create_task(connection.async_set_persistent_connection_enabled(False))
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
    old_sender = valve.clients[0].services.get_characteristic(NOTIFY)
    assert old_sender is not None
    assert connection.dashboard_data.water_usage == 111
    await clock.advance(31)
    valve.dashboards.append([])
    poll = asyncio.create_task(connection.async_poll())
    try:
        await clock.drain()
        assert len(valve.clients) == 2
        assert valve.clients[1].notifications
        for packet in dashboard_frames(gallons=222):
            old_handler(old_sender, bytearray(packet))
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
