"""Versioned fields follow the app while ordinary readings stay available."""

import pytest

from packets import LEGACY_310, MODERN_422, TWIN_122, dashboard_frames, device_list_frame


def prepare_valve(connection_factory, bluetooth_io, transport, source, firmware, twin=False, valve_type=1):
    if source == "advertisement" and firmware is not None:
        payload = bytearray(TWIN_122 if twin else LEGACY_310 if firmware < 412 else MODERN_422)
        payload[-2:] = bytes((firmware // 100, int(f"{firmware % 100:02d}", 16)))
        payload[2] = int(twin or firmware >= 419)
        payload[11 if len(payload) == 14 else 8] = valve_type
        bluetooth_io.advertise(manufacturer_data={1850: bytes(payload[2:])})
    else:
        bluetooth_io.advertise(manufacturer_data={})
    current = connection_factory()
    valve = transport.valve()
    valve.firmware_version = firmware
    valve.is_twin_valve = twin
    legacy = firmware is not None and firmware < 419 and not twin
    valve.authentication_required = not legacy
    response = bytearray(device_list_frame(
        status=112 if legacy else 128 if firmware is not None else 0,
        firmware=firmware if source == "device_list" else None, twin=twin,
    ))
    if legacy:
        response[7:12] = bytes.fromhex("70101e241c")
    # Zero leaves the type unknown in the response, exercising the trusted
    # advertisement fallback independently of the DeviceList mapping.
    response[3] = valve_type if source == "device_list" else 0
    valve.device_lists.append([bytes(response)])
    return current, valve


@pytest.mark.parametrize("source", ["advertisement", "device_list"])
@pytest.mark.parametrize("firmware,twin,flags,soak", [
    (209, False, False, (False, 0)),
    (210, False, False, (True, 255)),
    (409, False, False, (True, 255)),
    (410, False, True, (True, 255)),
    (122, True, True, (True, 255)),
    (None, False, False, (False, 0)),
])
async def test_optional_fields_follow_firmware_boundaries_without_losing_readings(
    connection_factory, bluetooth_io, transport, clock, source, firmware, twin, flags, soak,
):
    # Water System 6.18.2 parsePacket1 gates flags at 410; parsePacket2 gates
    # prefill/soak at 210. Twins bypass both thresholds. Reserved bytes in old
    # firmware must not invent operating states, even when those bytes are set.
    current, valve = prepare_valve(connection_factory, bluetooth_io, transport, source, firmware, twin)
    frames = dashboard_frames()
    first, second = map(bytearray, frames[:2])
    first[7:11] = (1234).to_bytes(2, "big") + (2345).to_bytes(2, "big")
    first[13:15] = (4567).to_bytes(2, "big")
    first[15] = 21
    first[18] = 0x1F
    second[10] = 0x08
    second[11] = 255
    frames[:2] = map(bytes, (first, second))
    valve.dashboards.append(frames)
    await clock.run(current.async_poll())
    data = current.dashboard_data
    assert data is not None
    assert (
        data.shutoff_setting_enabled, data.bypass_setting_enabled,
        data.shutoff_active, data.bypass_active, data.display_off,
    ) == (flags,) * 5
    assert (data.prefill_soak_mode, data.soak_timer) == soak
    assert (data.time_hour, data.time_minute, data.water_usage) == (7, 23, 567)
    assert (data.present_flow, data.peak_flow, data.water_hardness) == (12.34, 45.67, 21)
    assert data.water_remaining_until_regeneration == 2345
    assert current.last_success is not None


@pytest.mark.parametrize("source", ["advertisement", "device_list"])
@pytest.mark.parametrize("valve_type,wire_state,expected", [
    (1, 0, False), (13, 0, False), (14, 0, True), (15, 0, True),
    (14, 1, False), (15, 1, False), (16, 0, False), (0, 0, False),
])
async def test_aeration_state_applies_only_to_nitro_pro_sidekick(
    connection_factory, bluetooth_io, transport, clock, source, valve_type, wire_state, expected,
):
    # parsePacket2 updates isInAeration only for consolidated NitroProSidekick
    # (full valve types 14/15); bit 0 of byte 12 is inverted for that family.
    current, valve = prepare_valve(
        connection_factory, bluetooth_io, transport, source, 422, valve_type=valve_type,
    )
    frames = dashboard_frames()
    second = bytearray(frames[1])
    second[12] = wire_state
    frames[1] = bytes(second)
    valve.dashboards.append(frames)
    await clock.run(current.async_poll())
    assert current.dashboard_data is not None
    assert current.dashboard_data.is_in_aeration is expected
    assert current.dashboard_data.water_usage == 567


async def test_current_device_list_fields_take_precedence_over_older_advertisement(
    connection_factory, bluetooth_io, transport, clock,
):
    payload = bytearray(LEGACY_310)
    payload[2] = 0
    payload[8] = 1
    payload[-2:] = b"\x04\x10"
    bluetooth_io.advertise(manufacturer_data={1850: bytes(payload[2:])})
    current = connection_factory()
    valve = transport.valve()
    valve.authentication_required = False
    response = bytearray(device_list_frame(status=112, firmware=209))
    response[3] = 14
    response[7:12] = bytes.fromhex("70101e241c")
    valve.device_lists.append([bytes(response)])
    frames = dashboard_frames()
    first, second = map(bytearray, frames[:2])
    first[18] = 0x1F
    second[10:12] = b"\x08\xff"
    frames[:2] = map(bytes, (first, second))
    valve.dashboards.append(frames)
    await clock.run(current.async_poll())
    data = current.dashboard_data
    assert data is not None
    assert not data.display_off
    assert (data.prefill_soak_mode, data.soak_timer) == (False, 0)
    assert data.is_in_aeration


async def test_optional_session_fields_do_not_leak_into_the_next_poll(
    connection_factory, bluetooth_io, transport, clock,
):
    current, valve = prepare_valve(
        connection_factory, bluetooth_io, transport, "device_list", 422, valve_type=14,
    )
    frames = dashboard_frames(gallons=135)
    first, second = map(bytearray, frames[:2])
    first[18] = 0x1F
    second[10:12] = b"\x08\xff"
    frames[:2] = map(bytes, (first, second))
    valve.dashboards.append(frames)
    await clock.run(current.async_poll())
    assert current.dashboard_data.display_off
    assert current.dashboard_data.is_in_aeration
    assert current.dashboard_data.soak_timer == 255
    await clock.advance(31)
    valve.device_lists.append([device_list_frame(status=128)])
    frames[0] = frames[0][:11] + (246).to_bytes(2, "big") + frames[0][13:]
    valve.dashboards.append(frames)
    await clock.run(current.async_poll())
    data = current.dashboard_data
    assert data.water_usage == 246
    assert not data.display_off
    assert not data.is_in_aeration
    assert (data.prefill_soak_mode, data.soak_timer) == (False, 0)
