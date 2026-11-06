"""Authentication and fresh data through production polling and simulated GATT."""

from dataclasses import replace
import logging
import pytest
from custom_components.chandler_legacy_view.models import ValveAdvertisement
from packets import (
    CLASSIC_PASSWORD_VECTORS, LOGGED_SHORT_ADVERTISEMENTS,
    classic_device_list_frame, device_list_frame,
)


def partial():
    return ValveAdvertisement(address="valve-a", name="CS_Meter_Soft", rssi=-50,
        manufacturer_data={}, service_data={}, manufacturer_data_complete=False)


def classic():
    return replace(partial(), model="Evb019", firmware_version=418,
        manufacturer_data_complete=True, has_connection_counter=False,
        authentication_required=False)


def assert_dashboard(connection, valve):
    data = connection.dashboard_data
    assert data is not None
    assert (data.time_hour, data.time_minute, data.water_usage) == (7, 23, 567)
    assert connection.last_success is not None
    assert all(not client.notifications for client in valve.clients)


async def test_partial_startup_authenticates_and_retrieves_dashboard(connection_factory, transport, clock):
    connection = connection_factory(advertisement=partial())
    valve = transport.valve()
    await clock.run(connection.async_poll())
    assert len(valve.authentication_payloads) == 1
    assert valve.dashboard_requests == 1
    assert_dashboard(connection, valve)
    assert not connection.authentication_lockout
    assert not valve.clients[-1].is_connected


async def test_logged_short_packet_startup_authenticates(connection_factory, discovery_manager, bluetooth_io, transport, clock):
    observation = bluetooth_io.advertise(raw=bytes.fromhex(LOGGED_SHORT_ADVERTISEMENTS[0]), manufacturer_data={})
    advertisement = discovery_manager.devices["valve-a"]
    assert advertisement.firmware_version is None
    connection = connection_factory(advertisement=advertisement)
    assert bluetooth_io.history["valve-a"] is observation
    assert not discovery_manager.devices["valve-a"].manufacturer_data_complete
    transport.valve().authenticated(counter=0)
    await clock.run(connection.async_poll())
    assert_dashboard(connection, transport.valve())


async def test_trusted_counter_metadata_accepts_authenticated_status(connection_factory, transport, clock):
    connection = connection_factory()
    valve = transport.valve()
    valve.device_lists.append([device_list_frame(status=128)])
    await clock.run(connection.async_poll())
    assert valve.authentication_payloads == []
    assert_dashboard(connection, valve)


async def test_eighteen_byte_device_list_supports_authentication(connection_factory, transport, clock):
    connection = connection_factory(advertisement=partial())
    valve = transport.valve()
    valve.device_lists.append([device_list_frame(status=0)[:18]])
    valve.auth_replies.append([device_list_frame(status=128)[:18]])
    await clock.run(connection.async_poll())
    assert len(valve.authentication_payloads) == 1
    assert_dashboard(connection, valve)


async def test_unknown_authenticated_status_cannot_establish_protocol(connection_factory, transport, clock):
    # Legacy status 128 can encode a valid password; it cannot identify the
    # counter protocol or grant access to an otherwise unknown valve.
    connection = connection_factory(advertisement=partial())
    await connection.async_set_persistent_connection_enabled(True)
    valve = transport.valve()
    valve.device_lists.append([classic_device_list_frame(status=128)])
    await clock.run(connection.async_poll())
    assert valve.authentication_payloads == []
    assert valve.dashboard_requests == 0
    assert connection.dashboard_data is None
    assert not connection.authentication_lockout
    assert not valve.clients[-1].is_connected


async def test_malformed_device_list_does_not_authenticate_or_lock_out(connection_factory, transport, clock):
    connection = connection_factory(advertisement=partial())
    valve = transport.valve()
    packet = device_list_frame(status=0)
    valve.device_lists.append([packet[:length] for length in (3, 8, 12, 17)])
    await clock.run(connection.async_poll())
    assert valve.authentication_payloads == []
    assert valve.dashboard_requests == 0
    assert not connection.authentication_lockout
    assert valve.clients[-1].notifications == {}


@pytest.mark.parametrize("known_protocol", [False, True])
async def test_unrecognized_device_status_does_not_try_pin(connection_factory, transport, clock, known_protocol):
    connection = connection_factory(advertisement=None if known_protocol else partial())
    valve = transport.valve()
    valve.device_lists.append([device_list_frame(status=1)])
    await clock.run(connection.async_poll())
    assert valve.authentication_payloads == []
    assert valve.dashboard_requests == 0
    assert not connection.authentication_lockout


@pytest.mark.parametrize("reply", [device_list_frame(status=0)[:12], device_list_frame(status=1)])
async def test_unusable_authentication_reply_does_not_lock_out_pin(connection_factory, transport, clock, reply):
    connection = connection_factory(advertisement=partial())
    valve = transport.valve()
    valve.auth_replies.extend([[reply]] * 4)
    await clock.run(connection.async_poll())
    assert len(valve.authentication_payloads) >= 1
    assert valve.dashboard_requests == 0
    assert not connection.authentication_lockout
    assert valve.clients[-1].notifications == {}


@pytest.mark.parametrize("stale_unprotected", [False, True])
async def test_repeated_valid_rejections_lock_out_pin(connection_factory, transport, clock, stale_unprotected):
    advertisement = replace(partial(), model="Evb019", firmware_version=422,
                            authentication_required=not stale_unprotected)
    connection = connection_factory(advertisement=advertisement)
    await connection.async_set_persistent_connection_enabled(True)
    valve = transport.valve()
    valve.auth_replies.extend([[device_list_frame(status=0)]] * 4)
    await clock.run(connection.async_poll())
    assert len(valve.authentication_payloads) == 4
    assert valve.dashboard_requests == 0
    assert connection.authentication_lockout
    assert not valve.clients[-1].is_connected


async def test_classic_metadata_retains_legacy_dashboard_access(connection_factory, transport, clock):
    connection = connection_factory(advertisement=classic())
    valve = transport.valve()
    valve.authentication_required = False
    valve.device_lists.append([classic_device_list_frame()])
    await clock.run(connection.async_poll())
    assert valve.authentication_payloads == []
    assert_dashboard(connection, valve)


@pytest.mark.parametrize("pin,encoded", CLASSIC_PASSWORD_VECTORS)
async def test_signed_classic_response_allows_dashboard(
    connection_factory, transport, clock, pin, encoded,
):
    # A signed-byte decode failure must not strand an otherwise unprotected
    # legacy valve without readings or send unnecessary authentication writes.
    connection = connection_factory(advertisement=classic(), passcode=pin)
    valve = transport.valve()
    valve.authentication_required = False
    response = bytearray(device_list_frame(status=encoded[0]))
    response[7:12] = encoded
    valve.device_lists.append([bytes(response)])
    await clock.run(connection.async_poll())
    assert_dashboard(connection, valve)
    assert valve.authentication_payloads == []
    assert not connection.authentication_lockout


@pytest.mark.parametrize("encoded", [bytes(5), bytes.fromhex("70ffffffff")])
async def test_invalid_classic_decode_never_falls_back_to_pin_attempts(
    connection_factory, transport, clock, encoded,
):
    connection = connection_factory(advertisement=classic())
    valve = transport.valve()
    for _ in range(4):
        response = bytearray(device_list_frame(status=encoded[0]))
        response[7:12] = encoded
        valve.device_lists.append([bytes(response)])
        await clock.run(connection.async_poll())
        await clock.advance(31)
    assert valve.authentication_payloads == []
    assert valve.dashboard_requests == 0
    assert not connection.authentication_lockout


async def test_partial_update_keeps_established_counter_protocol(connection_factory, transport, clock):
    connection = connection_factory(advertisement=partial())
    valve = transport.valve()
    valve.authenticated(counter=255)
    await clock.run(connection.async_poll())
    await clock.advance(31)
    connection.update_from_advertisement(replace(partial(), connection_counter=0))
    valve.device_lists.append([device_list_frame(status=128, counter=0)])
    await clock.run(connection.async_poll())
    assert len(valve.authentication_payloads) == 1
    assert valve.dashboard_requests == 2
    assert valve.clients[0] is not valve.clients[1]
    assert_dashboard(connection, valve)


async def test_new_session_cannot_reuse_previous_authentication(connection_factory, transport, clock):
    connection = connection_factory(advertisement=partial())
    valve = transport.valve()
    await clock.run(connection.async_poll())
    previous_success = connection.last_success
    await clock.advance(31)
    valve.device_lists.append([])
    await clock.run(connection.async_poll())
    assert len(valve.authentication_payloads) == 1
    assert valve.dashboard_requests == 1
    assert len(valve.clients) == 2
    assert valve.clients[0] is not valve.clients[1]
    assert all(not client.is_connected for client in valve.clients)
    assert connection.last_success == previous_success


async def test_last_success_requires_fresh_dashboard(connection_factory, transport, clock):
    connection = connection_factory()
    valve = transport.valve()
    await clock.run(connection.async_poll())
    first_success = connection.last_success
    for response in ([device_list_frame(status=1)], [], [device_list_frame(status=128)]):
        await clock.advance(31)
        valve.device_lists.append(response)
        valve.dashboards.append([])
        await clock.run(connection.async_poll())
        assert connection.last_success == first_success
    assert valve.dashboard_requests == 2
    assert len(valve.clients) == 4
    assert all(not client.is_connected for client in valve.clients)


async def test_unresolved_first_poll_has_no_success_timestamp(connection_factory, transport, clock):
    connection = connection_factory(advertisement=partial())
    valve = transport.valve()
    valve.device_lists.append([device_list_frame(status=128)])
    await clock.run(connection.async_poll())
    assert valve.dashboard_requests == 0
    assert connection.dashboard_data is None
    assert connection.last_success is None
    assert not valve.clients[-1].is_connected


@pytest.mark.parametrize("passcode,advertisement,response", [
    ("1357", partial(), device_list_frame(status=0)),
    ("1357", classic(), classic_device_list_frame()),
    ("invalid-secret", partial(), device_list_frame(status=0)),
])
async def test_logs_omit_configured_and_decoded_pin(connection_factory, transport, clock, caplog, passcode, advertisement, response):
    caplog.set_level(logging.DEBUG)
    connection = connection_factory(passcode=passcode, advertisement=advertisement)
    valve = transport.valve()
    valve.pin = "1357"
    valve.authentication_required = advertisement.authentication_required is not False
    valve.device_lists.append([response])
    await clock.run(connection.async_poll())
    assert passcode not in caplog.text
    assert "digits=" not in caplog.text
    for payload in valve.authentication_payloads:
        assert payload.hex() not in caplog.text


async def test_incorrect_pin_cannot_publish_dashboard(connection_factory, transport, clock):
    connection = connection_factory(passcode="2468")
    valve = transport.valve()
    await clock.run(connection.async_poll())
    assert connection.authentication_lockout
    assert connection.dashboard_data is None
    assert connection.last_success is None
    assert valve.dashboard_requests == 0
    assert len(valve.authentication_payloads) == 4
    assert not valve.clients[-1].is_connected
