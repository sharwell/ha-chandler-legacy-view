"""C6.x authentication/recovery through production connections and HA state."""

import asyncio
import pytest

from homeassistant.helpers import entity_registry as er
from custom_components.chandler_legacy_view.const import DOMAIN, DATA_CONNECTION_MANAGER
from custom_components.chandler_legacy_view.const import (
    CONF_DEFAULT_PASSCODE, CONF_DEVICE_PASSCODES, CONF_DEVICE_ADDRESS, CONF_DEVICE_PASSCODE,
)
from evb034_packets import frames
from packets import EVB034_622


async def setup_valve(hass, runtime_factory, bluetooth_io, transport):
    entry = await runtime_factory()
    bluetooth_io.advertise(payload=EVB034_622)
    await hass.async_block_till_done()
    valve = transport.valve()
    valve.firmware_version = 622
    connection = hass.data[DOMAIN][entry.entry_id][DATA_CONNECTION_MANAGER].get_connection("valve-a")
    registry = er.async_get(hass)
    lockout = registry.async_get_entity_id("switch", DOMAIN, "valve-a_authentication_lockout")
    return entry, connection, valve, lockout


@pytest.mark.parametrize("failure,locked", [("wrong-pin", True), ("missing", False),
                                            ("unknown", False), ("malformed", False)])
async def test_only_explicit_rejection_locks_out_credentials(
    hass, runtime_factory, bluetooth_io, transport, clock, failure, locked,
):
    _, connection, valve, lockout = await setup_valve(hass, runtime_factory, bluetooth_io, transport)
    if failure == "wrong-pin":
        valve.pin = "2468"
    else:
        valve.auth_replies.append([] if failure == "missing" else
                                 frames({"as": 0} if failure == "unknown" else b"{bad}"))
    await clock.run(connection.async_poll())
    await hass.async_block_till_done()
    assert hass.states.get(lockout).state == ("on" if locked else "off")
    assert connection.last_success is None
    assert valve.dashboard_notifications == 0
    assert not valve.clients[0].is_connected


async def test_lockout_clear_and_reconnect_use_a_new_counter(
    hass, runtime_factory, bluetooth_io, transport, clock,
):
    _, connection, valve, lockout = await setup_valve(hass, runtime_factory, bluetooth_io, transport)
    valve.pin = "2468"
    await clock.run(connection.async_poll())
    assert connection.authentication_lockout
    await clock.advance(61)
    await clock.run(connection.async_poll())
    assert len(valve.authentication_payloads) == 2
    valve.pin = "1234"
    await hass.services.async_call("switch", "turn_off", {"entity_id": lockout}, blocking=True)
    await clock.advance(61)
    await clock.run(connection.async_poll())
    assert connection.last_success is not None
    assert connection.evb034_packet["dlc"] == 25
    assert not connection.authentication_lockout
    assert len(valve.authentication_payloads) == 3


@pytest.mark.parametrize("mtu,pin", [(23, "1234"), (64, "1357"), (247, "0000"), (517, "9999")])
async def test_configured_pin_authenticates_with_mtu_aware_shuffled_bytes(
    connection_factory, bluetooth_io, transport, clock, mtu, pin,
):
    bluetooth_io.advertise(payload=EVB034_622)
    valve = transport.valve()
    valve.firmware_version, valve.mtu_size, valve.pin = 622, mtu, pin
    connection = connection_factory(passcode=pin)
    await clock.run(connection.async_poll())
    assert connection.last_success is not None
    assert connection.evb034_packet["dwu"] == 567
    assert len(valve.authentication_payloads) == 1
    assert all(not client.is_connected for client in valve.clients)


async def test_cancellation_during_authentication_does_not_lock_out_or_reuse_reply(
    hass, runtime_factory, bluetooth_io, transport, clock,
):
    _, connection, valve, lockout = await setup_valve(hass, runtime_factory, bluetooth_io, transport)
    gate = valve.operations["authenticate"].block()
    poll = asyncio.create_task(connection.async_poll())
    await clock.run(gate.started.wait())
    callback = valve.clients[0].notification_history[0]
    poll.cancel()
    with pytest.raises(asyncio.CancelledError):
        await clock.run(poll)
    assert not connection.authentication_lockout
    assert not valve.clients[0].is_connected
    gate.release.set()
    await clock.advance(61)
    for frame in frames({"as": 2, "dlc": 200}):
        callback(None, bytearray(frame))
    await clock.run(connection.async_poll())
    assert connection.evb034_packet["dlc"] == 24
    assert connection.last_success is not None
    assert hass.states.get(lockout).state == "off"


async def test_live_session_survives_expiry_and_unload_releases_simulated_owner(
    hass, runtime_factory, bluetooth_io, transport, clock,
):
    entry, connection, valve, _ = await setup_valve(hass, runtime_factory, bluetooth_io, transport)
    await connection.async_set_persistent_connection_enabled(True)
    await clock.run(connection.async_poll())
    session = valve.clients[0]
    bluetooth_io.expire()
    await hass.async_block_till_done()
    assert connection.available
    valve.dashboards.append(frames({"dwu": 789}))
    await clock.until(lambda: connection.evb034_packet.get("dwu") == 789)
    assert valve.clients == [session]
    assert session.is_connected
    await clock.run(hass.config_entries.async_unload(entry.entry_id))
    assert not session.is_connected
    assert session.disconnects == 1
    assert not session.notifications
    assert not connection.available


async def test_per_valve_pin_correction_reloads_and_recovers_the_newer_session(
    hass, entry_factory, runtime_factory, bluetooth_io, transport, clock,
):
    entry = entry_factory(data={CONF_DEFAULT_PASSCODE: "9876"},
                          options={CONF_DEVICE_PASSCODES: {"valve-a": "2468"}})
    await runtime_factory(entry=entry)
    bluetooth_io.advertise(payload=EVB034_622)
    await hass.async_block_till_done()
    valve = transport.valve()
    valve.firmware_version = 622
    manager = hass.data[DOMAIN][entry.entry_id][DATA_CONNECTION_MANAGER]
    original = manager.get_connection("valve-a")
    await clock.run(original.async_poll())
    assert original.authentication_lockout
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(flow["flow_id"], user_input={
        CONF_DEVICE_ADDRESS: "valve-a", CONF_DEVICE_PASSCODE: "1234",
    })
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    replacement = hass.data[DOMAIN][entry.entry_id][DATA_CONNECTION_MANAGER].get_connection("valve-a")
    assert replacement is not original
    await clock.run(replacement.async_poll())
    assert replacement.last_success is not None
    assert not replacement.authentication_lockout
    assert entry.options[CONF_DEVICE_PASSCODES]["valve-a"] == "1234"
    assert entry.data[CONF_DEFAULT_PASSCODE] == "9876"
    assert all(not client.is_connected for client in valve.clients)


async def test_lost_authentication_stops_persistent_reads_and_reauthenticates(
    hass, runtime_factory, bluetooth_io, transport, clock,
):
    _, connection, valve, _ = await setup_valve(hass, runtime_factory, bluetooth_io, transport)
    await connection.async_set_persistent_connection_enabled(True)
    await hass.async_start()
    await clock.until(lambda: connection.last_success is not None)
    valve.dashboards.append(frames({"as": 1}))
    await clock.until(lambda: len(valve.clients) > 1 or not valve.clients[0].is_connected)
    assert not connection.authentication_lockout
    await clock.until(lambda: len(valve.clients) == 2 and valve.dashboard_notifications >= 3)
    assert len(valve.authentication_payloads) == 2
    assert connection.evb034_packet["dlc"] == 24
    await clock.run(connection.async_set_persistent_connection_enabled(False))
    assert all(not client.is_connected for client in valve.clients)


@pytest.mark.parametrize("counter", [0, 255, 256, 0x7FFFFFFF])
async def test_password_uses_the_identification_counters_low_byte(
    connection_factory, bluetooth_io, transport, clock, counter,
):
    bluetooth_io.advertise(payload=EVB034_622)
    valve = transport.valve()
    valve.firmware_version, valve.connection_counter = 622, counter
    connection = connection_factory()
    await clock.run(connection.async_poll())
    assert connection.last_success is not None
    assert connection.evb034_packet["dlc"] == counter
