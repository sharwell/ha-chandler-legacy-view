"""Compatibility logs require previous success, not merely rejected packets."""

from dataclasses import replace
import logging

import pytest

from custom_components.chandler_legacy_view.models import ValveAdvertisement
from packets import MODERN_422, classic_device_list_frame, dashboard_frames, device_list_frame

LOGGER = "custom_components.chandler_legacy_view.connection"


def compatibility_records(caplog):
    return [record for record in caplog.records if record.name == LOGGER
            and record.getMessage().startswith("Protocol compatibility:")]


def legacy_advertisement(firmware=418):
    return ValveAdvertisement(address="valve-a", name="CS_Meter_Soft", rssi=-50,
        manufacturer_data={}, service_data={}, model="Evb019", firmware_version=firmware,
        authentication_required=False, valve_type="MeteredSoftener")


def partial_advertisement():
    return ValveAdvertisement(address="valve-a", name="CS_Meter_Soft", rssi=-50,
        manufacturer_data={}, service_data={}, manufacturer_data_complete=False)


@pytest.mark.parametrize("variant", ["first19", "second19", "tail5", "tail7", "tail20", "late_header"])
async def test_previous_complete_response_rejected_now_warns_without_enabling_debug(
    connection_factory, transport, clock, caplog, variant,
):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    current = connection_factory()
    frames = dashboard_frames()
    if variant in ("first19", "second19"):
        index = int(variant == "second19")
        frames[index] = frames[index][:-2] + frames[index][-1:]
    elif variant.startswith("tail"):
        length = int(variant[4:])
        frames[5] = bytes(length - 1) + b":"
    else:
        # The previous collector allowed header 1 after graph header 2.
        frames[1], frames[2] = frames[2], frames[1]
    transport.valve().dashboards.append(frames)
    await clock.run(current.async_poll())
    assert current.dashboard_data is None
    assert current.last_success is None
    records = compatibility_records(caplog)
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert "previous Dashboard framing accepted a complete response" in records[0].getMessage()
    assert "previous_lengths=" in records[0].getMessage()


@pytest.mark.parametrize("variant", ["short18", "bad_terminator", "empty"])
async def test_response_that_neither_collector_accepts_has_no_compatibility_log(
    connection_factory, transport, clock, caplog, variant,
):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    frames = dashboard_frames()
    if variant == "short18":
        frames[0] = frames[0][:-3] + frames[0][-1:]
    elif variant == "bad_terminator":
        frames[5] = bytes(6)
    else:
        frames = []
    current = connection_factory()
    transport.valve().dashboards.append(frames)
    await clock.run(current.async_poll())
    assert current.dashboard_data is None
    assert compatibility_records(caplog) == []


@pytest.mark.parametrize("protected", [False, True])
async def test_unsigned_only_password_warns_only_for_previously_unprotected_access(
    connection_factory, transport, clock, caplog, protected,
):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    current = connection_factory(advertisement=replace(
        legacy_advertisement(), authentication_required=protected,
    ), passcode="2468")
    valve = transport.valve()
    valve.authentication_required = False
    response = bytearray(device_list_frame(status=144))
    # Before alignment these unsigned fields decoded as 1234. Signed Dalvik
    # division rejects them, so this is the reverse of the signed-byte fix.
    response[7:12] = bytes.fromhex("902a527590")
    valve.device_lists.append([bytes(response)])
    await clock.run(current.async_poll())
    assert current.dashboard_data is None
    records = compatibility_records(caplog)
    if protected:
        assert records == []
    else:
        assert len(records) == 1
        assert records[0].levelno == logging.WARNING
        assert "DeviceList allowed Dashboard access" in records[0].getMessage()
    assert "1234" not in caplog.text
    assert "2468" not in caplog.text
    assert response.hex() not in caplog.text
    assert "902a527590" not in caplog.text


@pytest.mark.parametrize("known_protocol", [False, True])
@pytest.mark.parametrize("status", [0, 128])
async def test_rejected_firmware_warns_only_with_previous_access_evidence(
    connection_factory, transport, clock, caplog, status, known_protocol,
):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    current = connection_factory(advertisement=None if known_protocol else partial_advertisement())
    response = bytearray(device_list_frame(status=status))
    response[5:7] = b"\x04\x1a"
    transport.valve().device_lists.append([bytes(response)])
    await clock.run(current.async_poll())
    assert current.dashboard_data is None
    records = compatibility_records(caplog)
    expected_warning = known_protocol and status == 128
    assert bool(records) is expected_warning
    if expected_warning:
        assert records[0].levelno == logging.WARNING
        assert "firmware_fields=(4, 26)" in records[0].getMessage()


@pytest.mark.parametrize("firmware,change,expected_fields", [
    (309, "flags", ["shutoff_setting_enabled"]),
    (209, "soak", ["prefill_soak_mode", "soak_timer"]),
    (422, "aeration", ["is_in_aeration"]),
])
async def test_both_decoders_succeed_but_changed_fields_are_debug_only(
    connection_factory, transport, clock, caplog, firmware, change, expected_fields,
):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    current = connection_factory(advertisement=legacy_advertisement(firmware))
    valve = transport.valve()
    if firmware < 419:
        valve.authentication_required = False
        valve.device_lists.append([classic_device_list_frame()])
    frames = dashboard_frames()
    first, second = map(bytearray, frames[:2])
    second[12] = 1
    if change == "flags":
        first[18] = 1
    elif change == "soak":
        second[10:12] = b"\x08\x07"
    else:
        second[12] = 0
    frames[:2] = map(bytes, (first, second))
    valve.dashboards.append(frames)
    await clock.run(current.async_poll())
    assert current.dashboard_data.water_usage == 567
    assert current.last_success is not None
    records = compatibility_records(caplog)
    assert len(records) == 1
    assert records[0].levelno == logging.DEBUG
    assert f"changed fields={expected_fields}" in records[0].getMessage()


async def test_duplicate_header_changes_graph_selection_at_debug_only(
    connection_factory, transport, clock, caplog,
):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    current = connection_factory()
    frames = dashboard_frames()
    second = bytearray(frames[1])
    second[12] = 1
    frames[1] = bytes(second)
    frames.insert(3, frames[0])
    transport.valve().dashboards.append(frames)
    await clock.run(current.async_poll())
    assert current.dashboard_data is not None
    records = compatibility_records(caplog)
    assert len(records) == 1
    assert records[0].levelno == logging.DEBUG
    assert "graph_usage_ten_gallons" in records[0].getMessage()


async def test_changed_device_list_protocol_that_still_works_is_debug_only(
    connection_factory, transport, clock, caplog,
):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    current = connection_factory(advertisement=legacy_advertisement())
    valve = transport.valve()
    valve.authentication_required = False
    response = bytearray(classic_device_list_frame(status=128))
    response[5:7] = b"\x04\x22"
    valve.device_lists.append([bytes(response)])
    await clock.run(current.async_poll())
    assert current.dashboard_data is not None
    records = compatibility_records(caplog)
    assert records
    assert all(record.levelno == logging.DEBUG for record in records)
    assert any("DeviceList succeeds under both" in record.getMessage() for record in records)
    assert "1357" not in caplog.text


@pytest.mark.parametrize("improvement", ["signed_pin", "metadata", "counter419", "graph_header"])
async def test_newly_supported_exchanges_do_not_generate_compatibility_messages(
    connection_factory, transport, bluetooth_io, clock, caplog, improvement,
):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    valve = transport.valve()
    frames = dashboard_frames()
    if improvement == "signed_pin":
        current = connection_factory(advertisement=legacy_advertisement())
        valve.authentication_required = False
        response = bytearray(device_list_frame(status=92))
        response[7:12] = bytes.fromhex("5cf6ead9c0")
        valve.device_lists.append([bytes(response)])
    elif improvement == "metadata":
        current = connection_factory(advertisement=partial_advertisement())
        valve.device_lists.append([device_list_frame(status=128, firmware=422)])
    elif improvement == "counter419":
        payload = bytearray(MODERN_422)
        payload[-1] = 0x19
        payload[6] = 23
        bluetooth_io.advertise(manufacturer_data={1850: bytes(payload[2:])})
        current = connection_factory()
        valve.firmware_version = 419
        valve.legacy_reserved_counter_byte = 42
    else:
        current = connection_factory()
        frames[3] = b"uu\x00" + bytes(17)
    valve.dashboards.append(frames)
    await clock.run(current.async_poll())
    assert current.dashboard_data is not None
    assert current.last_success is not None
    assert compatibility_records(caplog) == []
    if improvement == "metadata":
        # Capabilities inferred only by the new decoder are not independent
        # evidence that the old decoder would accept a later partial update.
        current.update_from_advertisement(replace(
            partial_advertisement(), has_connection_counter=True, authentication_required=True,
        ))
        await clock.advance(31)
        valve.device_lists.append([device_list_frame(status=128, firmware=422)])
        await clock.run(current.async_poll())
        assert compatibility_records(caplog) == []


async def test_equal_successful_interpretations_are_silent(
    connection_factory, transport, clock, caplog,
):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    current = connection_factory()
    frames = dashboard_frames()
    second = bytearray(frames[1])
    second[12] = 1
    frames[1] = bytes(second)
    transport.valve().dashboards.append(frames)
    await clock.run(current.async_poll())
    assert current.dashboard_data is not None
    assert compatibility_records(caplog) == []


async def test_new_access_does_not_make_rejected_framing_a_previous_success(
    connection_factory, transport, clock, caplog,
):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    current = connection_factory(advertisement=partial_advertisement())
    valve = transport.valve()
    valve.device_lists.append([device_list_frame(status=128, firmware=422)])
    frames = dashboard_frames()
    frames[5] = bytes(4) + b":"
    valve.dashboards.append(frames)
    await clock.run(current.async_poll())
    assert current.dashboard_data is None
    assert compatibility_records(caplog) == []


async def test_changed_access_interpretation_is_silent_when_both_dashboards_fail(
    connection_factory, transport, clock, caplog,
):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    current = connection_factory(advertisement=legacy_advertisement())
    valve = transport.valve()
    valve.authentication_required = False
    response = bytearray(classic_device_list_frame(status=128))
    response[5:7] = b"\x04\x22"
    valve.device_lists.append([bytes(response)])
    frames = dashboard_frames()
    frames[5] = bytes(6)
    valve.dashboards.append(frames)
    await clock.run(current.async_poll())
    assert current.dashboard_data is None
    assert compatibility_records(caplog) == []
