"""DeviceList evidence can recover sessions without rewriting advertisements."""

from dataclasses import replace

import pytest
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.chandler_legacy_view.const import (
    DATA_CONNECTION_MANAGER, DATA_DISCOVERY_MANAGER, DOMAIN,
)
from custom_components.chandler_legacy_view.models import ValveAdvertisement
from packets import dashboard_frames, device_list_frame


def partial():
    return ValveAdvertisement(address="valve-a", name="CS_Meter_Soft", rssi=-50,
        manufacturer_data={}, service_data={}, manufacturer_data_complete=False)


@pytest.mark.parametrize("firmware,twin,status,legacy", [
    (420, False, 128, None), (422, False, 128, None), (122, True, 128, None),
    (418, False, 92, bytes.fromhex("5cf6ead9c0")),
])
@pytest.mark.parametrize("length", [18, 20])
async def test_device_list_version_recovers_published_readings_without_advertisement_metadata(
    hass, runtime_factory, bluetooth_io, transport, clock, firmware, twin, status, legacy, length,
):
    # CsDeviceListPacket.update calls getFirmwareData before choosing the
    # authentication decoder. Status 128 alone is ambiguous; its validated
    # DeviceList firmware supplies the missing evidence for a partial startup.
    entry = await runtime_factory()
    bluetooth_io.advertise(manufacturer_data={})
    await hass.async_block_till_done()
    valve = transport.valve()
    valve.firmware_version = firmware
    valve.is_twin_valve = twin
    valve.authentication_required = legacy is None
    response = bytearray(device_list_frame(status=status, firmware=firmware, twin=twin))
    if legacy is not None:
        response[7:12] = legacy
    valve.device_lists.append([bytes(response[:length])])
    valve.dashboards.append(dashboard_frames(gallons=987))
    connection = hass.data[DOMAIN][entry.entry_id][DATA_CONNECTION_MANAGER].get_connection("valve-a")
    await clock.run(connection.async_poll())
    registry = er.async_get(hass)
    usage = registry.async_get_entity_id("sensor", DOMAIN, "valve-a_water_usage_today")
    assert hass.states.get(usage).state == "987"
    assert connection.last_success is not None
    assert valve.authentication_payloads == []
    assert not connection.authentication_lockout
    advertisement = hass.data[DOMAIN][entry.entry_id][DATA_DISCOVERY_MANAGER].devices["valve-a"]
    assert advertisement.firmware_version is None
    assert advertisement.model is None
    assert not advertisement.manufacturer_data_complete
    device = dr.async_get(hass).async_get(registry.async_get(usage).device_id)
    assert device.sw_version is None


async def test_device_list_version_corrects_stale_classic_protocol_for_this_session(
    connection_factory, transport, clock,
):
    connection = connection_factory(advertisement=replace(
        partial(), model="Evb019", firmware_version=418, authentication_required=False,
    ))
    valve = transport.valve()
    valve.firmware_version = 422
    await clock.run(connection.async_poll())
    assert connection.dashboard_data is not None
    assert connection.last_success is not None
    assert len(valve.authentication_payloads) == 1
    assert not connection.authentication_lockout


async def test_device_list_c419_still_requires_an_advertisement_counter(
    connection_factory, transport, clock,
):
    connection = connection_factory(advertisement=partial())
    valve = transport.valve()
    valve.firmware_version = 419
    await clock.run(connection.async_poll())
    assert valve.authentication_payloads == []
    assert connection.dashboard_data is None
    assert not connection.authentication_lockout


@pytest.mark.parametrize("major,minor", [(0, 0x22), (255, 0x22), (4, 0x1A), (4, 0xA1), (6, 0x22)])
@pytest.mark.parametrize("status", [0, 128])
async def test_malformed_or_unsupported_device_list_firmware_does_not_grant_access(
    connection_factory, transport, clock, major, minor, status,
):
    connection = connection_factory(advertisement=partial())
    response = bytearray(device_list_frame(status=status))
    response[5:7] = bytes((major, minor))
    valve = transport.valve()
    valve.device_lists.append([bytes(response)])
    await clock.run(connection.async_poll())
    assert valve.authentication_payloads == []
    assert valve.dashboard_requests == 0
    assert connection.last_success is None
    assert not connection.authentication_lockout


@pytest.mark.parametrize("minor", [0xFA, 0xFF])
async def test_device_list_beta_minor_uses_the_apps_99_sentinel(
    connection_factory, transport, clock, minor,
):
    connection = connection_factory(advertisement=partial())
    response = bytearray(device_list_frame(status=128, firmware=499))
    response[6] = minor
    valve = transport.valve()
    valve.device_lists.append([bytes(response)])
    await clock.run(connection.async_poll())
    assert connection.dashboard_data is not None
    assert valve.authentication_payloads == []


@pytest.mark.parametrize("status", [1, 127, 129, 255])
async def test_known_device_list_firmware_does_not_turn_unknown_status_into_authentication(
    connection_factory, transport, clock, status,
):
    connection = connection_factory(advertisement=partial())
    valve = transport.valve()
    valve.device_lists.append([device_list_frame(status=status, firmware=422)])
    await clock.run(connection.async_poll())
    assert valve.authentication_payloads == []
    assert connection.dashboard_data is None
    assert not connection.authentication_lockout


async def test_legacy_session_metadata_does_not_grant_access_on_a_later_connection(
    connection_factory, transport, clock,
):
    connection = connection_factory(advertisement=partial())
    valve = transport.valve()
    valve.authentication_required = False
    for firmware in (418, None):
        response = bytearray(device_list_frame(status=92, firmware=firmware))
        response[7:12] = bytes.fromhex("5cf6ead9c0")
        valve.device_lists.append([bytes(response)])
    await clock.run(connection.async_poll())
    first_success = connection.last_success
    assert first_success is not None
    await clock.advance(31)
    await clock.run(connection.async_poll())
    assert valve.dashboard_requests == 1
    assert connection.last_success == first_success
    assert not connection.authentication_lockout
