"""Existing HA entities publish C6.x values using independent wire fixtures."""

from datetime import datetime
import pytest

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.helpers import entity_registry as er, device_registry as dr
from homeassistant.util import dt as dt_util
from custom_components.chandler_legacy_view.const import DOMAIN, DATA_CONNECTION_MANAGER
from packets import EVB034_622
from evb034_packets import frames


def sensor_id(hass, suffix):
    return er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"valve-a_{suffix}")


async def setup_readings(hass, entry_factory, runtime_factory, bluetooth_io, transport,
                         firmware=622, valve_type=1, partial=False, **identity):
    entry = entry_factory()
    er.async_get(hass).async_get_or_create("sensor", DOMAIN, "valve-a_time_of_day",
                                         config_entry=entry, disabled_by=None)
    await runtime_factory(entry=entry)
    bluetooth_io.advertise(**({"manufacturer_data": {}} if partial else {"payload": EVB034_622}))
    await hass.async_block_till_done()
    valve = transport.valve()
    valve.firmware_version = firmware
    valve.identification_packets.append(frames({
        "dlf": firmware, "dlc": 23, "as": 1, "dlvt": valve_type,
        "dli": 0, "dlic": 0, "gbre": 1, **identity,
    }))
    connection = hass.data[DOMAIN][entry.entry_id][DATA_CONNECTION_MANAGER].get_connection("valve-a")
    return entry, connection, valve


@pytest.mark.parametrize("firmware,hour,usage,remaining", [
    (600, 11, 12345, 45678), (608, 11, 12345, 45678),
    (609, 23, 12345, 45678), (610, 23, 12345, 45678),
    (611, 23, 123.45, 456), (622, 23, 123.45, 456),
])
async def test_existing_entities_match_field_specific_conversion_boundaries(
    hass, entry_factory, runtime_factory, bluetooth_io, transport, clock,
    firmware, hour, usage, remaining,
):
    _, connection, valve = await setup_readings(
        hass, entry_factory, runtime_factory, bluetooth_io, transport, firmware,
    )
    valve.dashboards.append(frames({"dh": hour, "dm": 37, "da": 1 if firmware < 609 else 0,
        "dbl": 9500, "gpf": 1234, "dpfd": 5678, "dwu": 12345,
        "dtgr": 45678, "dwh": 26, "dcdo": 7}))
    await clock.run(connection.async_poll())
    await hass.async_block_till_done()
    expected = {"present_flow": 12.34, "peak_flow_today": 56.78,
                "water_usage_today": usage, "soft_water_remaining": remaining,
                "water_hardness": 26, "battery": 100}
    for suffix, value in expected.items():
        assert float(hass.states.get(sensor_id(hass, suffix)).state) == value
    assert hass.states.get(sensor_id(hass, "present_flow")).attributes["unit_of_measurement"] == "gal/min"
    assert hass.states.get(sensor_id(hass, "water_usage_today")).attributes["unit_of_measurement"] == "gal"
    assert hass.states.get(sensor_id(hass, "days_until_regeneration")).state == "unavailable"
    published = dt_util.as_local(datetime.fromisoformat(hass.states.get(sensor_id(hass, "time_of_day")).state))
    assert (published.hour, published.minute) == (23, 37)


async def test_non_metered_days_uses_current_countdown_instead_of_configured_frequency(
    hass, entry_factory, runtime_factory, bluetooth_io, transport, clock,
):
    _, connection, valve = await setup_readings(
        hass, entry_factory, runtime_factory, bluetooth_io, transport, valve_type=4,
    )
    valve.dashboards.append(frames({"dcdo": 3, "ddo": 19, "dwu": 12500}))
    await clock.run(connection.async_poll())
    assert hass.states.get(sensor_id(hass, "days_until_regeneration")).state == "3"
    hardness = sensor_id(hass, "water_hardness")
    assert hardness is None or hass.states.get(hardness).state == "unavailable"


async def test_partial_startup_learns_identity_and_preserves_missing_values_as_unknown(
    hass, entry_factory, runtime_factory, bluetooth_io, transport, clock,
):
    entry, connection, valve = await setup_readings(
        hass, entry_factory, runtime_factory, bluetooth_io, transport, partial=True,
        dlsa=0x1234, dlsb=0xABCD,
    )
    usage = sensor_id(hass, "water_usage_today")
    assert sensor_id(hass, "water_hardness") is None
    valve.dashboards.append(frames({"dwu": 12500}))
    await clock.run(connection.async_poll())
    await hass.async_block_till_done()
    assert sensor_id(hass, "water_usage_today") == usage
    assert float(hass.states.get(usage).state) == 125
    for suffix in ("present_flow", "time_of_day", "water_hardness", "battery", "soft_water_remaining"):
        assert hass.states.get(sensor_id(hass, suffix)).state == "unknown"
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, "valve-a"), entry.entry_id)
    assert device.serial_number == "1234ABCD"
    assert device.sw_version == "C6.22"
    assert device.model == "Evb034"
    assert device.identifiers == {(DOMAIN, "valve-a")}


async def test_persistent_partial_updates_retain_session_values_but_reconnect_does_not_invent_them(
    hass, entry_factory, runtime_factory, bluetooth_io, transport, clock,
):
    _, connection, valve = await setup_readings(hass, entry_factory, runtime_factory, bluetooth_io, transport)
    usage = sensor_id(hass, "water_usage_today")
    sequence = []
    remove = hass.bus.async_listen(EVENT_STATE_CHANGED, lambda event:
        sequence.append(event.data["new_state"].state)
        if event.data["entity_id"] == usage and event.data["new_state"] else None)
    await connection.async_set_persistent_connection_enabled(True)
    valve.dashboards.extend([frames({"dwu": 12300, "gpf": 150}), frames({"dwu": 45600}), frames({"gpf": 275})])
    try:
        await clock.run(connection.async_poll())
        await clock.until(lambda: connection.evb034_packet.get("gpf") == 275)
        await hass.async_block_till_done()
        assert float(hass.states.get(usage).state) == 456
        assert float(hass.states.get(sensor_id(hass, "present_flow")).state) == 2.75
        assert [float(value) for value in sequence if value not in ("unknown", "unavailable")] == [123, 456]
        await clock.run(connection.async_set_persistent_connection_enabled(False))
        await clock.advance(61)
        valve.dashboards.append(frames({"gpf": 375}))
        await clock.run(connection.async_poll())
        assert hass.states.get(usage).state == "unknown"
        assert float(hass.states.get(sensor_id(hass, "present_flow")).state) == 3.75
    finally:
        remove()


async def test_battery_read_is_enabled_like_the_app_without_creating_a_control(
    hass, entry_factory, runtime_factory, bluetooth_io, transport, clock,
):
    _, connection, valve = await setup_readings(
        hass, entry_factory, runtime_factory, bluetooth_io, transport, gbre=0,
    )
    valve.dashboards.append(frames({"dbl": 9500}))
    await clock.run(connection.async_poll())
    assert b'{"gbre":1}' in valve.packet_requests
    assert hass.states.get(sensor_id(hass, "battery")).state == "100"


@pytest.mark.parametrize("invalid", [None, True, "123", -1, 2**40])
async def test_missing_or_invalid_reading_is_not_published_as_a_real_zero(
    hass, entry_factory, runtime_factory, bluetooth_io, transport, clock, invalid,
):
    _, connection, valve = await setup_readings(hass, entry_factory, runtime_factory, bluetooth_io, transport)
    valve.dashboards.append(frames({"dwu": invalid, "gpf": 250}))
    await clock.run(connection.async_poll())
    assert hass.states.get(sensor_id(hass, "water_usage_today")).state == "unknown"
    assert float(hass.states.get(sensor_id(hass, "present_flow")).state) == 2.5


async def test_current_status_updates_existing_bypass_and_low_salt_entities(
    hass, entry_factory, runtime_factory, bluetooth_io, transport, clock,
):
    _, connection, valve = await setup_readings(hass, entry_factory, runtime_factory, bluetooth_io, transport)
    valve.dashboards.append(frames({"dwu": 0, "gvs": 0xA0}))
    await clock.run(connection.async_poll())
    for suffix in ("bypass", "salt"):
        identifier = er.async_get(hass).async_get_entity_id("binary_sensor", DOMAIN, f"valve-a_{suffix}")
        assert hass.states.get(identifier).state == "on"
    assert float(hass.states.get(sensor_id(hass, "water_usage_today")).state) == 0
