"""Repeated entry cancellation drains real teardown and simulated Bluetooth I/O."""

import asyncio
import logging
from homeassistant.helpers import entity_registry as er
from custom_components.chandler_legacy_view.const import DOMAIN, DATA_CONNECTION_MANAGER, DATA_DISCOVERY_MANAGER


async def test_repeated_cancellation_releases_session_and_discovery(hass, runtime_factory, bluetooth_io, transport, clock, caplog):
    entry = await runtime_factory()
    bluetooth_io.advertise()
    await hass.async_block_till_done()
    runtime = hass.data[DOMAIN][entry.entry_id]
    manager = runtime[DATA_CONNECTION_MANAGER]
    discovery = runtime[DATA_DISCOVERY_MANAGER]
    connection = manager.get_connection("valve-a")
    await connection.async_set_persistent_connection_enabled(True)
    await connection.async_poll()
    await clock.drain()
    bluetooth_io.expire()
    other = await runtime_factory("other")
    other_runtime = hass.data[DOMAIN][other.entry_id]
    valve = transport.valve()
    gate = valve.operations["disconnect"].block()
    identifier = er.async_get(hass).async_get_entity_id("binary_sensor", DOMAIN, "valve-a")
    caplog.clear()
    unload = asyncio.create_task(hass.config_entries.async_unload(entry.entry_id))
    try:
        await clock.run(gate.started.wait())
        remaining_subscriptions = len(bluetooth_io.advertisement_callbacks)
        for _ in range(3):
            unload.cancel()
            await clock.drain()
            assert not unload.done()
            assert valve.clients[0].is_connected
            assert manager.get_connection("valve-a") is connection
            assert "valve-a" in discovery.devices
            assert len(bluetooth_io.advertisement_callbacks) == remaining_subscriptions
            assert len(bluetooth_io.unavailable_callbacks) == 1
            assert hass.data[DOMAIN][other.entry_id] is other_runtime
    finally:
        gate.release.set()
        result = await clock.run(asyncio.gather(unload, return_exceptions=True))
    assert isinstance(result[0], asyncio.CancelledError)
    assert not valve.clients[0].is_connected
    assert valve.clients[0].disconnects == 1
    assert valve.clients[0].notifications == {}
    assert list(manager.get_connections()) == []
    assert discovery.devices == {}
    assert bluetooth_io.unavailable_callbacks == []
    assert len(bluetooth_io.advertisement_callbacks) * 2 == remaining_subscriptions
    assert hass.data[DOMAIN] == {other.entry_id: other_runtime}
    # HA retains an unavailable registry placeholder after unloading an enabled
    # entity. It must not look like a live valve or receive later updates.
    assert hass.states.get(identifier).state == "unavailable"
    assert hass.states.get(identifier).attributes["restored"] is True
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]
    # No released timer/listener may recreate the unloaded entry's connection.
    await clock.advance(901)
    assert list(manager.get_connections()) == []
    assert len(valve.clients) == 1
    assert hass.states.get(identifier).attributes["restored"] is True
