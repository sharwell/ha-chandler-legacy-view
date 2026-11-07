"""Variant flags and conversions follow the reference's public HA outcomes."""

from datetime import datetime
import pytest

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from custom_components.chandler_legacy_view.const import DOMAIN
from evb034_packets import frames
from packets import EVB034_622
from test_evb034_entities import setup_readings, sensor_id


@pytest.mark.parametrize("clack,valve_type,twin,series,expected_type,metered", [
    (0, 1, 0, 2, "MeteredSoftener", True),
    (0, 17, 0, 6, "CommercialMeteredSoftener", True),
    (0, 18, 0, 5, "CommercialBackwashingFilter", False),
    (0, 4, 1, 7, "BackwashingFilter", True),
    (0, 2, 0, 3, "TimeClockSoftener", False),
    (1, 1, 0, 1, "MeteredSoftener", True),
    (1, 2, 0, 2, "BackwashingFilter", False),
    (1, 3, 0, 3, "ClackAeration", False),
    (1, 4, 0, 4, "MeteredSoftener", True),
    (1, 5, 0, 5, "BackwashingFilter", False),
    (1, 6, 0, 6, "MeteredSoftener", True),
    (1, 7, 0, 7, "BackwashingFilter", False),
    (1, 8, 0, 7, "MeteredSoftener", True),
    (1, 9, 1, 7, "BackwashingFilter", True),
    (0, 999, 0, 99, "Unknown", False),
])
async def test_reported_variant_and_series_match_existing_readings_and_applicability(
    hass, entry_factory, runtime_factory, bluetooth_io, transport, clock,
    clack, valve_type, twin, series, expected_type, metered,
):
    _, connection, valve = await setup_readings(
        hass, entry_factory, runtime_factory, bluetooth_io, transport, 608, valve_type,
        dlic=clack, dli=twin, dlvs=series, dlcf=list(b"2.7A"),
    )
    valve.dashboards.append(frames({"dwu": 12345, "dtgr": 45678, "dcdo": 3,
        "dh": 0 if clack else 12, "dm": 41, "da": 1 if clack else 0,
        "dbl": 9500, "gpf": 125, "dpfd": 975, "dwh": 21}))
    await clock.run(connection.async_poll())
    await hass.async_block_till_done()
    assert float(hass.states.get(sensor_id(hass, "water_usage_today")).state) == (123.45 if clack else 12345)
    assert float(hass.states.get(sensor_id(hass, "present_flow")).state) == 1.25
    assert float(hass.states.get(sensor_id(hass, "peak_flow_today")).state) == 9.75
    assert hass.states.get(sensor_id(hass, "battery")).state == ("unavailable" if clack else "100")
    assert hass.states.get(sensor_id(hass, "days_until_regeneration")).state == ("unavailable" if metered else "3")
    hardness = sensor_id(hass, "water_hardness")
    if metered:
        assert hass.states.get(hardness).state == "21"
        assert float(hass.states.get(sensor_id(hass, "soft_water_remaining")).state) == (456 if clack else 45678)
    else:
        assert hardness is None or hass.states.get(hardness).state == "unavailable"
    presence = er.async_get(hass).async_get_entity_id("binary_sensor", DOMAIN, "valve-a")
    attributes = hass.states.get(presence).attributes
    assert attributes["valve_type"] == expected_type
    assert attributes["is_clack_valve"] == bool(clack)
    assert attributes["clack_firmware_version"] == "2.7A"
    assert attributes["valve_series_version"] == series
    if series <= 7:
        assert attributes["valve_series_version_ebx044_display"] == f"Series {series}"
    else:
        assert "valve_series_version_ebx044_display" not in attributes
    timestamp = dt_util.as_local(datetime.fromisoformat(hass.states.get(sensor_id(hass, "time_of_day")).state))
    assert (timestamp.hour, timestamp.minute) == (0, 41)


async def test_confirmed_variant_survives_advertisements_and_partial_packet_updates(
    hass, entry_factory, runtime_factory, bluetooth_io, transport, clock,
):
    _, connection, valve = await setup_readings(
        hass, entry_factory, runtime_factory, bluetooth_io, transport, 608, 4,
        dlic=1, dli=1, dlvs=7, gbre=0,
    )
    await connection.async_set_persistent_connection_enabled(True)
    valve.dashboards.append(frames({"dwu": 12345, "dwh": 24}))
    await clock.run(connection.async_poll())
    assert b'{"gbre":1}' not in valve.packet_requests
    battery = sensor_id(hass, "battery")
    hardness = sensor_id(hass, "water_hardness")
    published = []
    # Record on the event loop so a delayed executor callback cannot hide
    # an incorrect availability transition from the assertions below.
    @callback
    def record_battery_change(event):
        if event.data["entity_id"] == battery and event.data["new_state"] is not None:
            published.append(event.data["new_state"].state)

    remove = hass.bus.async_listen(EVENT_STATE_CHANGED, record_battery_change)
    try:
        for observation in ({"payload": EVB034_622}, {"manufacturer_data": {}}):
            bluetooth_io.advertise(**observation)
            await hass.async_block_till_done()
            assert hass.states.get(battery).state == "unavailable"
            assert hass.states.get(hardness).state == "24"
        valve.dashboards.append(frames({"dwu": 23456, "dlic": None, "dli": None}))
        await clock.until(lambda: connection.evb034_packet.get("dwu") == 23456)
        assert float(hass.states.get(sensor_id(hass, "water_usage_today")).state) == 234.56
        assert all(value == "unavailable" for value in published)
    finally:
        remove()
        await clock.run(connection.async_set_persistent_connection_enabled(False))


async def test_variant_change_publishes_availability_without_replacing_entities(
    hass, entry_factory, runtime_factory, bluetooth_io, transport, clock,
):
    _, connection, valve = await setup_readings(hass, entry_factory, runtime_factory, bluetooth_io, transport)
    await connection.async_set_persistent_connection_enabled(True)
    valve.dashboards.extend([frames({"dbl": 9500, "dwh": 17}),
                            frames({"dlic": 1, "dlvt": 2, "dcdo": 4, "dwu": 1200})])
    await clock.run(connection.async_poll())
    identifiers = {suffix: sensor_id(hass, suffix) for suffix in
                   ("battery", "water_hardness", "soft_water_remaining", "days_until_regeneration")}
    assert hass.states.get(identifiers["battery"]).state == "100"
    await clock.until(lambda: connection.evb034_packet.get("dlic") == 1)
    assert hass.states.get(identifiers["battery"]).state == "unavailable"
    assert hass.states.get(identifiers["water_hardness"]).state == "unavailable"
    assert hass.states.get(identifiers["soft_water_remaining"]).state == "unavailable"
    assert hass.states.get(identifiers["days_until_regeneration"]).state == "4"
    assert {suffix: sensor_id(hass, suffix) for suffix in identifiers} == identifiers
    await clock.run(connection.async_set_persistent_connection_enabled(False))


@pytest.mark.parametrize("raw,remaining,hour", [
    (0, 0, 0), (1, 0, 12), (99, 0, 23), (100, 1, 0),
    (101, 1, 12), (100599999, 1006000, 23),
])
async def test_clack_volume_and_clock_boundaries_use_the_reference_conversions(
    hass, entry_factory, runtime_factory, bluetooth_io, transport, clock, raw, remaining, hour,
):
    _, connection, valve = await setup_readings(
        hass, entry_factory, runtime_factory, bluetooth_io, transport, 600, 1, dlic=1,
    )
    valve.dashboards.append(frames({"dh": hour, "dm": 0, "da": 1,
                                    "dtgr": raw, "dwu": raw, "gpf": 1}))
    await clock.run(connection.async_poll())
    assert float(hass.states.get(sensor_id(hass, "soft_water_remaining")).state) == remaining
    assert float(hass.states.get(sensor_id(hass, "water_usage_today")).state) == raw / 100
    assert float(hass.states.get(sensor_id(hass, "present_flow")).state) == 0.01
    timestamp = dt_util.as_local(datetime.fromisoformat(hass.states.get(sensor_id(hass, "time_of_day")).state))
    assert timestamp.hour == hour


@pytest.mark.parametrize("invalid", [None, "1", True, 4])
async def test_unusable_variant_update_cannot_erase_confirmed_clack_support(
    hass, entry_factory, runtime_factory, bluetooth_io, transport, clock, invalid,
):
    _, connection, valve = await setup_readings(
        hass, entry_factory, runtime_factory, bluetooth_io, transport, 608, 1, dlic=1,
    )
    await connection.async_set_persistent_connection_enabled(True)
    valve.dashboards.extend([frames({"dwu": 12300}), frames({"dwu": 45600, "dlic": invalid})])
    await clock.run(connection.async_poll())
    await clock.until(lambda: connection.evb034_packet.get("dwu") == 45600)
    assert float(hass.states.get(sensor_id(hass, "water_usage_today")).state) == 456
    assert hass.states.get(sensor_id(hass, "battery")).state == "unavailable"
    await clock.run(connection.async_set_persistent_connection_enabled(False))


async def test_reported_non_clack_identity_can_enable_battery_despite_discovery_name(
    hass, entry_factory, runtime_factory, bluetooth_io, transport, clock,
):
    _, connection, valve = await setup_readings(
        hass, entry_factory, runtime_factory, bluetooth_io, transport,
        advertised_name="CL_01", dlic=0,
    )
    assert sensor_id(hass, "battery") is None
    valve.dashboards.append(frames({"dbl": 9500, "dwu": 12345}))
    await clock.run(connection.async_poll())
    await hass.async_block_till_done()
    battery = sensor_id(hass, "battery")
    assert hass.states.get(battery).state == "100"
    assert float(hass.states.get(sensor_id(hass, "water_usage_today")).state) == 123.45


async def test_missing_variant_on_reconnect_keeps_confirmed_clack_conversion_and_battery_exclusion(
    hass, entry_factory, runtime_factory, bluetooth_io, transport, clock,
):
    _, connection, valve = await setup_readings(
        hass, entry_factory, runtime_factory, bluetooth_io, transport, 608, 1, dlic=1,
    )
    valve.dashboards.append(frames({"dwu": 12345}))
    await clock.run(connection.async_poll())
    await clock.advance(31)
    valve.identification_packets.append(frames({"dlf": 608, "dlc": 24, "as": 1, "gbre": 0}))
    valve.dashboards.append(frames({"dwu": 45678}))
    await clock.run(connection.async_poll())
    assert float(hass.states.get(sensor_id(hass, "water_usage_today")).state) == 456.78
    assert hass.states.get(sensor_id(hass, "battery")).state == "unavailable"
    assert b'{"gbre":1}' not in valve.packet_requests


@pytest.mark.parametrize("delivery,clack", [
    ("auth-reply", 1), ("split-identification", 1), ("auth-reply", 0),
])
async def test_metadata_received_during_authentication_controls_battery_sampling(
    hass, entry_factory, runtime_factory, bluetooth_io, transport, clock, delivery, clack,
):
    _, connection, valve = await setup_readings(
        hass, entry_factory, runtime_factory, bluetooth_io, transport, 608, dlic=None, gbre=0,
    )
    if delivery == "split-identification":
        valve.identification_packets.clear()
        valve.identification_packets.append(
            frames({"dlf": 608, "dlc": 23})
            + frames({"as": 1, "dlic": clack, "dlvt": 1, "gbre": 0})
        )
    else:
        valve.auth_replies.append(frames({"as": 2, "dlic": clack, "gbre": 0}))
    if clack:
        # Clack must read successfully even when unsupported battery writes
        # would be rejected by the external valve boundary.
        valve.chunk_acknowledgements.extend([b"\xc8", b"\xc8"])
    valve.dashboards.append(frames({"dwu": 12345, "dbl": 9500}))
    await clock.run(connection.async_poll())
    await hass.async_block_till_done()
    assert connection.last_success is not None
    assert valve.packet_requests == ([] if clack else [b'{"gbre":1}'])
    assert float(hass.states.get(sensor_id(hass, "water_usage_today")).state) == (123.45 if clack else 12345)
    assert hass.states.get(sensor_id(hass, "battery")).state == ("unavailable" if clack else "100")
    presence = er.async_get(hass).async_get_entity_id("binary_sensor", DOMAIN, "valve-a")
    assert hass.states.get(presence).attributes["firmware_version"] == ("L6.08" if clack else "C6.08")


async def test_remaining_volume_rounding_saturates_at_the_reference_integer_limit(
    hass, entry_factory, runtime_factory, bluetooth_io, transport, clock,
):
    _, connection, valve = await setup_readings(
        hass, entry_factory, runtime_factory, bluetooth_io, transport, 608,
    )
    # Float rounding must not publish a volume beyond the reference's signed int.
    valve.dashboards.append(frames({"dtgr": 2147483647}))
    await clock.run(connection.async_poll())
    assert hass.states.get(sensor_id(hass, "soft_water_remaining")).state == "2147483647"


@pytest.mark.parametrize("raw,expected", [
    ([], ""), ([50, 46, 55, 65], "2.7A"), ([-61, -87], "é"), ([321], "A"),
])
async def test_controller_firmware_uses_the_reference_byte_array_decoding(
    hass, entry_factory, runtime_factory, bluetooth_io, transport, clock, raw, expected,
):
    _, connection, valve = await setup_readings(
        hass, entry_factory, runtime_factory, bluetooth_io, transport, dlcf=raw,
    )
    valve.dashboards.append(frames({"dwu": 100}))
    await clock.run(connection.async_poll())
    presence = er.async_get(hass).async_get_entity_id("binary_sensor", DOMAIN, "valve-a")
    assert hass.states.get(presence).attributes["clack_firmware_version"] == expected
