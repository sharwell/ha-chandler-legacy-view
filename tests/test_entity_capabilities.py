"""APK display conditions govern availability without changing entity creation.

Water System 6.18.2: CsDashboardViewModel.setupValveOptions(IZZZ...)
adds clock, flow, usage, and peak flow without firmware gates. It excludes
battery for Clack, selects hardness/remaining water for metered softeners,
and selects days for other valves. CsValveType$Companion.isMeteredSoftener
includes MeteredSoftener, CommercialMeteredSoftener, and every twin.
"""

import asyncio
import logging

import pytest
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.chandler_legacy_view.const import DATA_CONNECTION_MANAGER, DOMAIN
from packets import (
    EVB034_622,
    LEGACY_310,
    MODERN_422,
    TWIN_122,
    device_list_frame,
)


def advertise(bluetooth_io, firmware=422, valve_type=1, *, clack=False):
    """Send a complete scanner observation through the production discovery path."""

    if firmware >= 600:
        payload, type_index = bytearray(EVB034_622), 6
    elif 100 <= firmware < 200:
        payload, type_index = bytearray(TWIN_122), 11
    elif firmware >= 412:
        payload, type_index = bytearray(MODERN_422), 11
    else:
        payload, type_index = bytearray(LEGACY_310), 8
    payload[type_index] = valve_type
    payload[-2:] = bytes((firmware // 100, int(f"{firmware % 100:02d}", 16)))
    bluetooth_io.advertise(
        name="CL_01" if clack else "CS_Meter_Soft", payload=bytes(payload)
    )


def sensor_ids(hass):
    registry = er.async_get(hass)
    suffixes = (
        "time_of_day", "present_flow", "water_usage_today", "peak_flow_today",
        "battery", "water_hardness", "soft_water_remaining", "days_until_regeneration",
    )
    result = {
        suffix: registry.async_get_entity_id("sensor", DOMAIN, f"valve-a_{suffix}")
        for suffix in suffixes
    }
    assert all(result.values())
    return result


async def existing_dashboard_entities(hass, runtime_factory, bluetooth_io):
    """Let unchanged creation rules register both days and metered-only sensors."""

    entry = await runtime_factory()
    er.async_get(hass).async_get_or_create(
        "sensor", DOMAIN, "valve-a_time_of_day", config_entry=entry, disabled_by=None
    )
    bluetooth_io.advertise(manufacturer_data={})
    await hass.async_block_till_done()
    advertise(bluetooth_io)
    await hass.async_block_till_done()
    identifiers = sensor_ids(hass)
    return entry, identifiers


def connection(hass, entry):
    return hass.data[DOMAIN][entry.entry_id][DATA_CONNECTION_MANAGER].get_connection("valve-a")


@pytest.mark.parametrize("firmware,valve_type,clack,metered", [
    (209, 1, False, True),
    (210, 4, False, False),
    (409, 17, False, True),
    (410, 2, False, False),
    (418, 1, False, True),
    (419, 4, False, False),
    (420, 17, False, True),
    (422, 13, False, False),
    (622, 1, False, True),
    (122, 4, False, True),
    (422, 1, True, True),
    (422, 2, True, False),
])
async def test_existing_entities_follow_apk_display_conditions(
    hass, runtime_factory, bluetooth_io, firmware, valve_type, clack, metered,
):
    _, identifiers = await existing_dashboard_entities(hass, runtime_factory, bluetooth_io)
    advertise(bluetooth_io, firmware, valve_type, clack=clack)
    await hass.async_block_till_done()

    expected = {
        "time_of_day": True, "present_flow": True, "water_usage_today": True,
        "peak_flow_today": True, "battery": not clack,
        "water_hardness": metered, "soft_water_remaining": metered,
        "days_until_regeneration": not metered,
    }
    assert {
        suffix: hass.states.get(identifier).state != "unavailable"
        for suffix, identifier in identifiers.items()
    } == expected
    assert sensor_ids(hass) == identifiers


async def test_partial_startup_keeps_unknown_support_and_creation_unchanged(
    hass, runtime_factory, bluetooth_io,
):
    await runtime_factory()
    bluetooth_io.advertise(manufacturer_data={})
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    days = registry.async_get_entity_id("sensor", DOMAIN, "valve-a_days_until_regeneration")
    assert hass.states.get(days).state == "unknown"
    assert registry.async_get_entity_id("sensor", DOMAIN, "valve-a_water_hardness") is None
    assert registry.async_get_entity_id("sensor", DOMAIN, "valve-a_soft_water_remaining") is None


async def test_device_list_support_is_published_before_dashboard_arrives(
    hass, runtime_factory, bluetooth_io, transport, clock,
):
    entry, identifiers = await existing_dashboard_entities(hass, runtime_factory, bluetooth_io)
    valve = transport.valve()
    response = bytearray(device_list_frame(status=128, firmware=422))
    response[3] = 4
    valve.device_lists.append([bytes(response)])
    valve.dashboards.append([])
    gate = valve.operations["dashboard"].block()
    poll = asyncio.create_task(connection(hass, entry).async_poll())
    try:
        await clock.run(gate.started.wait())
        # Reaching a new DeviceList interpretation must hide inapplicable
        # readings even when the following Dashboard response never arrives.
        assert connection(hass, entry).dashboard_data is None
        assert hass.states.get(identifiers["water_hardness"]).state == "unavailable"
        assert hass.states.get(identifiers["soft_water_remaining"]).state == "unavailable"
        assert hass.states.get(identifiers["days_until_regeneration"]).state == "unknown"
    finally:
        gate.release.set()
        await clock.run(poll)
    assert connection(hass, entry).dashboard_data is None


@pytest.mark.parametrize("malformed", ["invalid_firmware", "short_packet"])
async def test_rejected_metadata_cannot_change_existing_entity_support(
    hass, runtime_factory, bluetooth_io, transport, clock, malformed,
):
    entry, identifiers = await existing_dashboard_entities(hass, runtime_factory, bluetooth_io)
    response = bytearray(device_list_frame(status=0, firmware=422))
    response[3] = 4
    if malformed == "invalid_firmware":
        response[6] = 0x1A
    else:
        response = response[:12]
    transport.valve().device_lists.append([bytes(response)])
    await clock.run(connection(hass, entry).async_poll())
    assert hass.states.get(identifiers["water_hardness"]).state == "unknown"
    assert hass.states.get(identifiers["soft_water_remaining"]).state == "unknown"
    assert hass.states.get(identifiers["days_until_regeneration"]).state == "unavailable"


async def test_twin_device_list_enables_metered_rows_without_changing_advertisement(
    hass, runtime_factory, bluetooth_io, transport, clock,
):
    entry, identifiers = await existing_dashboard_entities(hass, runtime_factory, bluetooth_io)
    advertise(bluetooth_io, valve_type=4)
    await hass.async_block_till_done()
    assert hass.states.get(identifiers["soft_water_remaining"]).state == "unavailable"
    response = bytearray(device_list_frame(status=128, firmware=122, twin=True))
    response[3] = 4
    transport.valve().device_lists.append([bytes(response)])
    await clock.run(connection(hass, entry).async_poll())
    assert hass.states.get(identifiers["soft_water_remaining"]).state == "0"
    assert hass.states.get(identifiers["water_hardness"]).state == "0"
    assert hass.states.get(identifiers["days_until_regeneration"]).state == "unavailable"
    assert sensor_ids(hass) == identifiers
    registry = er.async_get(hass)
    days = registry.async_get(identifiers["days_until_regeneration"])
    assert dr.async_get(hass).async_get(days.device_id).sw_version == "C4.22"


async def test_partial_updates_and_empty_fields_cannot_erase_confirmed_support(
    hass, runtime_factory, bluetooth_io, transport, clock,
):
    entry, identifiers = await existing_dashboard_entities(hass, runtime_factory, bluetooth_io)
    valve = transport.valve()
    response = bytearray(device_list_frame(status=128, firmware=422))
    response[3] = 4
    valve.device_lists.append([bytes(response)])
    await clock.run(connection(hass, entry).async_poll())
    remaining = identifiers["soft_water_remaining"]
    assert hass.states.get(remaining).state == "unavailable"
    published = []
    # Record on the event loop so a delayed executor callback cannot hide
    # an incorrect availability transition from the assertions below.
    @callback
    def record_remaining_change(event):
        if event.data["entity_id"] == remaining and event.data["new_state"] is not None:
            published.append(event.data["new_state"].state)

    remove = hass.bus.async_listen(EVENT_STATE_CHANGED, record_remaining_change)
    try:
        # Discovery merges the older metered advertisement into a short status
        # update. It must not override the newer non-metered DeviceList result.
        bluetooth_io.advertise(manufacturer_data={})
        await hass.async_block_till_done()
        await clock.advance(31)
        valve.device_lists.append([device_list_frame(status=128)])
        await clock.run(connection(hass, entry).async_poll())
        assert hass.states.get(remaining).state == "unavailable"
        assert all(state == "unavailable" for state in published)
        advertise(bluetooth_io, valve_type=1)
        await hass.async_block_till_done()
        assert hass.states.get(remaining).state == "0"
        assert sensor_ids(hass) == identifiers
    finally:
        remove()


async def test_disabled_entity_does_not_publish_capability_changes(
    hass, runtime_factory, bluetooth_io, transport, clock, caplog,
):
    entry, identifiers = await existing_dashboard_entities(hass, runtime_factory, bluetooth_io)
    hardness = identifiers["water_hardness"]
    registry = er.async_get(hass)
    registry.async_update_entity(hardness, disabled_by=er.RegistryEntryDisabler.USER)
    await hass.async_block_till_done()
    response = bytearray(device_list_frame(status=128, firmware=422))
    response[3] = 4
    transport.valve().device_lists.append([bytes(response)])
    caplog.clear()
    await clock.run(connection(hass, entry).async_poll())
    assert hass.states.get(hardness) is None
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert sensor_ids(hass) == identifiers


async def test_reachability_and_support_are_both_required(
    hass, runtime_factory, bluetooth_io,
):
    _, identifiers = await existing_dashboard_entities(hass, runtime_factory, bluetooth_io)
    days = identifiers["days_until_regeneration"]
    assert hass.states.get(days).state == "unavailable"
    bluetooth_io.expire()
    await hass.async_block_till_done()
    assert all(hass.states.get(identifier).state == "unavailable" for identifier in identifiers.values())
    advertise(bluetooth_io, valve_type=4)
    await hass.async_block_till_done()
    assert hass.states.get(days).state == "unknown"
    assert hass.states.get(identifiers["water_hardness"]).state == "unavailable"
    assert hass.states.get(identifiers["soft_water_remaining"]).state == "unavailable"
