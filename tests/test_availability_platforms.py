"""Availability contracts through HA's own platforms, registries, and events."""

import asyncio
import pytest
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from custom_components.chandler_legacy_view.const import DOMAIN, DATA_CONNECTION_MANAGER
from packets import MODERN_422


def entity_id(hass, domain, unique_id):
    result = er.async_get(hass).async_get_entity_id(domain, DOMAIN, unique_id)
    assert result is not None, (domain, unique_id)
    return result


def enabled_records(hass, entry, address):
    return [record for record in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
            if record.unique_id.startswith(address) and not record.disabled]


@pytest.mark.parametrize("already_discovered", [True, False])
async def test_all_platforms_publish_loss_and_recovery(hass, runtime_factory, bluetooth_io, transport, clock, already_discovered):
    if already_discovered:
        bluetooth_io.advertise("softener")
        bluetooth_io.advertise("filter", name="C2_04", payload=MODERN_422)
    entry = await runtime_factory()
    if not already_discovered:
        bluetooth_io.advertise("softener")
        bluetooth_io.advertise("filter", name="C2_04", payload=MODERN_422)
    await hass.async_block_till_done()
    softener = enabled_records(hass, entry, "softener")
    water_filter = enabled_records(hass, entry, "filter")
    assert {record.domain for record in softener} == {"binary_sensor", "sensor", "number", "switch"}
    assert {record.domain for record in water_filter} == {"binary_sensor", "sensor", "number", "switch"}
    original_ids = {record.entity_id for record in softener + water_filter}
    presence = entity_id(hass, "binary_sensor", "softener")
    assert hass.states.get(presence).state == "on"
    published = []
    # Unmarked synchronous listeners run in HA's executor and can lag behind
    # recovery. Record the transition sequence on the event loop.
    @callback
    def record_presence_change(event):
        if event.data["entity_id"] == presence and event.data["new_state"] is not None:
            published.append(event.data["new_state"].state)

    remove = hass.bus.async_listen(EVENT_STATE_CHANGED, record_presence_change)
    try:
        await clock.run(hass.async_start())
        await clock.until(lambda: all(transport.valve(address).clients and
            not transport.valve(address).clients[-1].is_connected for address in ("softener", "filter")))
        bluetooth_io.expire("softener")
        await hass.async_block_till_done()
        assert all(hass.states.get(record.entity_id).state == "unavailable" for record in softener)
        assert all(hass.states.get(record.entity_id).state != "unavailable" for record in water_filter)
        await clock.advance(901)
        await clock.until(lambda: len(transport.valve("filter").clients) == 2 and
                          not transport.valve("filter").clients[-1].is_connected)
        assert len(transport.valve("softener").clients) == 1
        bluetooth_io.expire("unknown")
        bluetooth_io.advertise("softener", rssi=-45)
        await clock.until(lambda: len(transport.valve("softener").clients) == 2 and
                          not transport.valve("softener").clients[-1].is_connected)
        assert all(hass.states.get(record.entity_id).state != "unavailable" for record in softener + water_filter)
        assert {record.entity_id for record in enabled_records(hass, entry, "softener") + enabled_records(hass, entry, "filter")} == original_ids
        assert published == ["unavailable", "on"]
    finally:
        remove()


async def test_live_session_defers_published_loss_until_cleanup(hass, runtime_factory, bluetooth_io, transport, clock):
    entry = await runtime_factory()
    bluetooth_io.advertise()
    await hass.async_block_till_done()
    await clock.run(hass.async_start())
    valve = transport.valve()
    await clock.until(lambda: valve.clients and not valve.clients[-1].is_connected)
    persistent = entity_id(hass, "switch", "valve-a_persistent_connection")
    presence = entity_id(hass, "binary_sensor", "valve-a")
    await hass.services.async_call("switch", "turn_on", {"entity_id": persistent}, blocking=True)
    await clock.until(lambda: len(valve.clients) == 2 and valve.clients[-1].is_connected)
    bluetooth_io.expire()
    await hass.async_block_till_done()
    assert hass.states.get(presence).state == "on"
    manager = hass.data[DOMAIN][entry.entry_id][DATA_CONNECTION_MANAGER]
    assert manager.get_connection("valve-a").available
    gate = valve.operations["disconnect"].block()
    disable = asyncio.create_task(hass.services.async_call("switch", "turn_off", {"entity_id": persistent}, blocking=True))
    try:
        await clock.run(gate.started.wait())
        assert not disable.done()
        assert hass.states.get(presence).state == "on"
        assert valve.clients[-1].is_connected
    finally:
        gate.release.set()
        await clock.run(disable)
    assert hass.states.get(presence).state == "unavailable"
    assert not manager.get_connection("valve-a").available
    bluetooth_io.advertise()
    await clock.until(lambda: len(valve.clients) == 3 and not valve.clients[-1].is_connected)
    assert hass.states.get(presence).state == "on"
    assert manager.get_connection("valve-a").available
    assert {"bluetooth", "usb", "http"}.isdisjoint(hass.config.components)
