"""HA lifecycle results remain truthful when a backend cannot release a valve."""

import asyncio

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from custom_components.chandler_legacy_view.const import (
    CONNECTION_TEARDOWN_BUDGET_SECONDS, DATA_CONNECTION_MANAGER, DOMAIN,
)
from packets import EVB034_622, dashboard_frames
from test_evb034_authentication import setup_valve


@pytest.mark.parametrize("evb034", [False, True])
async def test_reload_cancels_stalled_read_and_publishes_new_runtime_readings(
    hass, runtime_factory, bluetooth_io, transport, clock, evb034,
):
    if evb034:
        entry, old, valve, _ = await setup_valve(hass, runtime_factory, bluetooth_io, transport)
        gate = valve.operations["identify"].block()
    else:
        entry = await runtime_factory()
        bluetooth_io.advertise()
        await hass.async_block_till_done()
        old = hass.data[DOMAIN][entry.entry_id][DATA_CONNECTION_MANAGER].get_connection("valve-a")
        valve = transport.valve()
        gate = valve.operations["device_list"].block()
    poll = asyncio.create_task(old.async_poll())
    await clock.run(gate.started.wait())
    start = clock.loop.time()
    await clock.run(hass.config_entries.async_reload(entry.entry_id))
    assert clock.loop.time() - start < CONNECTION_TEARDOWN_BUDGET_SECONDS
    assert entry.state is ConfigEntryState.LOADED
    assert poll.cancelled()
    assert gate.cancelled
    assert not valve.clients[0].is_connected
    old_handler = list(valve.clients[0].notification_history)
    gate.release.set()
    bluetooth_io.advertise(**({"payload": EVB034_622} if evb034 else {}))
    await hass.async_block_till_done()
    replacement = hass.data[DOMAIN][entry.entry_id][DATA_CONNECTION_MANAGER].get_connection("valve-a")
    assert replacement is not old
    if evb034:
        from evb034_packets import frames
        valve.dashboards.append(frames({"dwu": 98700}))
    else:
        valve.dashboards.append(dashboard_frames(gallons=987))
    await clock.run(replacement.async_poll())
    assert replacement.dashboard_data.water_usage == 987
    usage = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, "valve-a_water_usage_today")
    assert float(hass.states.get(usage).state) == 987
    for handler in old_handler:
        for frame in dashboard_frames(gallons=111):
            handler(None, bytearray(frame))
    await clock.drain()
    assert float(hass.states.get(usage).state) == 987


@pytest.mark.parametrize("operation", ["connect", "device_list", "disconnect"])
async def test_quarantined_unload_blocks_replacement_until_late_release(
    hass, runtime_factory, bluetooth_io, transport, clock, operation, caplog,
):
    entry = await runtime_factory()
    bluetooth_io.advertise()
    await hass.async_block_till_done()
    runtime = hass.data[DOMAIN][entry.entry_id]
    old = runtime[DATA_CONNECTION_MANAGER].get_connection("valve-a")
    valve = transport.valve()
    gate = valve.operations[operation].block()
    gate.ignore_cancellation = True
    poll = asyncio.create_task(old.async_poll())
    await clock.run(gate.started.wait())
    start = clock.loop.time()
    # Reload reports setup pending rather than pretending the replacement is
    # ready. Always release test-controlled work if an assertion fails.
    try:
        await clock.run(hass.config_entries.async_reload(entry.entry_id))
    except BaseException:
        gate.release.set()
        raise
    assert clock.loop.time() - start < CONNECTION_TEARDOWN_BUDGET_SECONDS
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert "Old Bluetooth cleanup is unconfirmed" in entry.reason
    assert entry.entry_id not in hass.data.get(DOMAIN, {})
    assert "cleanup quarantined" in caplog.text
    assert old.cleanup_pending
    # A different entry can operate another valve while setup of the old
    # entry waits. Late cleanup cannot remove that entry's runtime.
    bluetooth_io.expire()
    second = await runtime_factory("second-entry")
    bluetooth_io.advertise("valve-b")
    await hass.async_block_till_done()
    manager = hass.data[DOMAIN][second.entry_id][DATA_CONNECTION_MANAGER]
    other = manager.get_connection("valve-b")
    await clock.run(other.async_poll())
    assert transport.attempts == ["valve-a", "valve-b"]
    assert other.last_success is not None
    gate.release.set()
    await clock.until(lambda: not old.cleanup_pending)
    await asyncio.gather(poll, return_exceptions=True)
    valve.dashboards.clear()
    valve.dashboards.append(dashboard_frames(gallons=987))
    assert hass.data[DOMAIN][second.entry_id][DATA_CONNECTION_MANAGER] is manager
    # Remove the separate runtime before rediscovering valve-a so its entity
    # IDs remain owned by the original entry throughout the replacement.
    assert await clock.run(hass.config_entries.async_unload(second.entry_id))
    bluetooth_io.advertise()
    assert await clock.run(hass.config_entries.async_reload(entry.entry_id))
    new = hass.data[DOMAIN][entry.entry_id][DATA_CONNECTION_MANAGER].get_connection("valve-a")
    await clock.run(new.async_poll())
    assert new.dashboard_data.water_usage == 987


async def test_switch_off_reports_unconfirmed_disconnect_and_keeps_off_state(
    hass, runtime_factory, bluetooth_io, transport, clock,
):
    entry = await runtime_factory()
    bluetooth_io.advertise()
    await hass.async_block_till_done()
    connection = hass.data[DOMAIN][entry.entry_id][DATA_CONNECTION_MANAGER].get_connection("valve-a")
    await connection.async_set_persistent_connection_enabled(True)
    await clock.run(connection.async_poll())
    gate = transport.valve().operations["disconnect"].block()
    gate.ignore_cancellation = True
    switch = er.async_get(hass).async_get_entity_id("switch", DOMAIN, "valve-a_persistent_connection")
    try:
        with pytest.raises(HomeAssistantError, match="quarantined"):
            await clock.run(hass.services.async_call("switch", "turn_off", {"entity_id": switch}, blocking=True))
        assert hass.states.get(switch).state == "off"
        assert connection.cleanup_pending
        await connection.async_set_persistent_connection_enabled(True)
        await connection.async_poll()
        assert transport.attempts == ["valve-a"]
    finally:
        gate.release.set()
        await clock.until(lambda: not connection.cleanup_pending)


async def test_evb034_failed_unsubscribe_still_disconnects_and_unloads(
    hass, runtime_factory, bluetooth_io, transport, clock,
):
    entry, connection, valve, _ = await setup_valve(hass, runtime_factory, bluetooth_io, transport)
    await connection.async_set_persistent_connection_enabled(True)
    await clock.run(connection.async_poll())
    gate = valve.operations["stop_notify"].block()
    start = clock.loop.time()
    assert await clock.run(hass.config_entries.async_unload(entry.entry_id))
    assert clock.loop.time() - start < CONNECTION_TEARDOWN_BUDGET_SECONDS
    assert gate.cancelled
    assert not valve.clients[0].is_connected
    assert all(task.done() for task in valve.clients[0].peer_tasks)


async def test_concurrent_switch_off_callers_join_the_same_cleanup(
    connection_factory, transport, clock,
):
    connection = connection_factory()
    await connection.async_set_persistent_connection_enabled(True)
    await clock.run(connection.async_poll())
    valve = transport.valve()
    gate = valve.operations["disconnect"].block()
    first = asyncio.create_task(connection.async_set_persistent_connection_enabled(False))
    await clock.run(gate.started.wait())
    second = asyncio.create_task(connection.async_set_persistent_connection_enabled(False))
    await clock.drain()
    assert not first.done() and not second.done()
    first.cancel()
    second.cancel()
    await clock.drain()
    assert not first.done() and not second.done()
    gate.release.set()
    results = await clock.run(asyncio.gather(first, second, return_exceptions=True))
    assert all(isinstance(result, asyncio.CancelledError) for result in results)
    assert valve.clients[0].disconnects == 1
    assert not valve.clients[0].is_connected
