"""Entry lifecycle contracts with real platforms, entries, and managers."""

import asyncio
import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er
from custom_components import chandler_legacy_view as integration
from custom_components.chandler_legacy_view.const import DOMAIN, DATA_CONNECTION_MANAGER, DATA_DISCOVERY_MANAGER


async def test_failed_platform_unload_preserves_runtime_for_retry(hass, runtime_factory, bluetooth_io, monkeypatch):
    entry = await runtime_factory()
    bluetooth_io.advertise()
    await hass.async_block_till_done()
    runtime = hass.data[DOMAIN][entry.entry_id]
    presence = er.async_get(hass).async_get_entity_id("binary_sensor", DOMAIN, "valve-a")
    async def refuse(entry, platforms):
        return False
    # Platform refusal is the behavior this module must handle. The real HA
    # entity platform catches entity-removal errors, so Bluetooth I/O cannot
    # produce this return value; inject it only at this public API boundary.
    with monkeypatch.context() as context:
        context.setattr(hass.config_entries, "async_unload_platforms", refuse)
        for _ in range(2):
            assert not await integration.async_unload_entry(hass, entry)
            assert hass.data[DOMAIN][entry.entry_id] is runtime
            assert hass.states.get(presence).state == "on"
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert DOMAIN not in hass.data
    assert bluetooth_io.advertisement_callbacks == []
    assert bluetooth_io.unavailable_callbacks == []


async def test_successful_unload_preserves_other_entry_and_discovery(hass, runtime_factory, bluetooth_io):
    first = await runtime_factory("first")
    other = await runtime_factory("other")
    other_runtime = hass.data[DOMAIN][other.entry_id]
    assert await hass.config_entries.async_unload(first.entry_id)
    assert first.state is ConfigEntryState.NOT_LOADED
    assert other.state is ConfigEntryState.LOADED
    assert hass.data[DOMAIN] == {other.entry_id: other_runtime}
    bluetooth_io.advertise()
    await hass.async_block_till_done()
    identifier = er.async_get(hass).async_get_entity_id("binary_sensor", DOMAIN, "valve-a")
    assert hass.states.get(identifier).state == "on"
    assert other_runtime[DATA_CONNECTION_MANAGER].get_connection("valve-a").available


async def test_unexpected_connection_cleanup_error_still_releases_discovery(hass, runtime_factory, bluetooth_io, monkeypatch, caplog):
    entry = await runtime_factory()
    runtime = hass.data[DOMAIN][entry.entry_id]
    manager = runtime[DATA_CONNECTION_MANAGER]
    unload = manager.async_unload
    async def unexpected_failure():
        await unload()
        raise RuntimeError("Unexpected cleanup failure")
    # Transport errors are handled by the production manager. This regression
    # specifically verifies the entry's finally block for an unexpected error.
    monkeypatch.setattr(manager, "async_unload", unexpected_failure)
    assert not await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.FAILED_UNLOAD
    assert "Unexpected cleanup failure" in caplog.text
    assert DOMAIN not in hass.data
    assert runtime[DATA_DISCOVERY_MANAGER].devices == {}
    assert bluetooth_io.advertisement_callbacks == []
    assert bluetooth_io.unavailable_callbacks == []


async def test_cancelled_platform_unload_keeps_runtime_and_states(hass, runtime_factory, bluetooth_io, monkeypatch):
    entry = await runtime_factory()
    bluetooth_io.advertise()
    await hass.async_block_till_done()
    runtime = hass.data[DOMAIN][entry.entry_id]
    identifier = er.async_get(hass).async_get_entity_id("binary_sensor", DOMAIN, "valve-a")
    async def cancelled(entry, platforms):
        raise asyncio.CancelledError
    with monkeypatch.context() as context:
        context.setattr(hass.config_entries, "async_unload_platforms", cancelled)
        with pytest.raises(asyncio.CancelledError):
            await integration.async_unload_entry(hass, entry)
    assert hass.data[DOMAIN][entry.entry_id] is runtime
    assert hass.states.get(identifier).state == "on"
    assert bluetooth_io.advertisement_callbacks
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_concurrent_entry_unloads_preserve_cancellation(hass, runtime_factory, bluetooth_io, transport, clock):
    first = await runtime_factory("first")
    bluetooth_io.advertise()
    await hass.async_block_till_done()
    manager = hass.data[DOMAIN][first.entry_id][DATA_CONNECTION_MANAGER]
    connection = manager.get_connection("valve-a")
    await connection.async_set_persistent_connection_enabled(True)
    await connection.async_poll()
    # The first valve stops advertising while its live session retains presence.
    # A later entry sees no cached valve and therefore owns no duplicate entities.
    bluetooth_io.expire()
    other = await runtime_factory("other")
    valve = transport.valve()
    gate = valve.operations["disconnect"].block()
    first_unload = asyncio.create_task(hass.config_entries.async_unload(first.entry_id))
    try:
        await clock.run(gate.started.wait())
        assert await hass.config_entries.async_unload(other.entry_id)
        first_unload.cancel()
        await clock.drain()
        assert not first_unload.done()
        assert valve.clients[0].is_connected
    finally:
        gate.release.set()
        results = await clock.run(asyncio.gather(first_unload, return_exceptions=True))
    assert isinstance(results[0], asyncio.CancelledError)
    assert DOMAIN not in hass.data
    assert bluetooth_io.advertisement_callbacks == []
    assert bluetooth_io.unavailable_callbacks == []
    assert not valve.clients[0].is_connected
