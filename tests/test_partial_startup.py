"""Partial observations must recover readings through the whole HA runtime."""

from datetime import datetime

import pytest
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.chandler_legacy_view.const import DOMAIN, DATA_DISCOVERY_MANAGER
from packets import LOGGED_SHORT_ADVERTISEMENTS, dashboard_frames


@pytest.mark.parametrize("already_discovered", [True, False], ids=["cached-before-setup", "observed-after-setup"])
@pytest.mark.parametrize("raw", [None, bytes.fromhex(LOGGED_SHORT_ADVERTISEMENTS[0])], ids=["no-manufacturer-data", "logged-short-packet"])
async def test_partial_startup_publishes_authenticated_readings(hass, entry_factory, runtime_factory, bluetooth_io, transport, clock, already_discovered, raw):
    entry = entry_factory()
    registry = er.async_get(hass)
    # Enable the optional clock through HA's persisted registry choice, so this
    # scenario proves both clock and usage recovery without firmware metadata.
    time_record = registry.async_get_or_create("sensor", DOMAIN, "valve-a_time_of_day",
                                               config_entry=entry, disabled_by=None)
    if already_discovered:
        observation = bluetooth_io.advertise(raw=raw, manufacturer_data={})
    await runtime_factory(entry=entry)
    if not already_discovered:
        observation = bluetooth_io.advertise(raw=raw, manufacturer_data={})
    await hass.async_block_till_done()

    presence = registry.async_get_entity_id("binary_sensor", DOMAIN, "valve-a")
    usage = registry.async_get_entity_id("sensor", DOMAIN, "valve-a_water_usage_today")
    assert presence is not None
    assert usage is not None
    assert hass.states.get(presence).state == "on"
    assert hass.states.get(usage).state == "unknown"
    assert hass.states.get(time_record.entity_id).state == "unknown"
    discovery = hass.data[DOMAIN][entry.entry_id][DATA_DISCOVERY_MANAGER]
    advertisement = discovery.devices["valve-a"]
    assert not advertisement.manufacturer_data_complete
    assert advertisement.firmware_version is None
    assert advertisement.has_connection_counter is None

    valve = transport.valve()
    valve.authenticated(counter=0)
    valve.dashboards.append(dashboard_frames(hour=9, minute=34, gallons=987))
    # Let HA startup schedule the production manager's first poll. No standalone
    # connection or metadata injection may make this path succeed instead.
    await clock.run(hass.async_start())
    await clock.until(lambda: hass.states.get(usage).state == "987"
                      and valve.clients and not valve.clients[-1].is_connected)
    published_time = datetime.fromisoformat(hass.states.get(time_record.entity_id).state)
    assert (published_time.hour, published_time.minute) == (9, 34)
    assert hass.states.get(presence).state == "on"
    assert valve.authentication_payloads
    assert len(valve.clients) == 1
    assert bluetooth_io.history["valve-a"] is observation
    assert not advertisement.manufacturer_data_complete
    assert advertisement.firmware_version is None
    device = dr.async_get(hass).async_get(registry.async_get(presence).device_id)
    assert device.sw_version is None
    assert {"bluetooth", "usb", "http"}.isdisjoint(hass.config.components)
