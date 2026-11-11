"""Display configuration uses authenticated wire commands and confirmed HA state."""

import asyncio

import pytest
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from custom_components.chandler_legacy_view.const import DATA_CONNECTION_MANAGER, DOMAIN
from custom_components.chandler_legacy_view.freshness import RECOVERY_ALLOWANCE
from ble import NOTIFY
from evb034_packets import frames
from packets import dashboard_frames
from test_configured_authentication import entity_id


async def setup_display(hass, runtime_factory, bluetooth_io, transport, clock,
                        firmware=440, twin=False, clack=False, report=True):
    entry = await runtime_factory()
    # Partial startup ensures entity eligibility comes from connected identity,
    # rather than a friendly name or a fabricated advertisement tail.
    bluetooth_io.advertise(manufacturer_data={})
    await hass.async_block_till_done()
    connection = hass.data[DOMAIN][entry.entry_id][DATA_CONNECTION_MANAGER].get_connection("valve-a")
    valve = transport.valve()
    valve.firmware_version = firmware
    valve.is_twin_valve = twin
    valve.report_display_off = report
    valve.authentication_required = firmware >= 419 or twin
    if firmware >= 600:
        valve.identification_packets.append(frames({
            "dlf": firmware, "dlc": 23, "as": 1, "gbre": 1,
            "dlvt": 1, "dli": int(twin), "dlic": int(clack),
        }))
    await clock.run(connection.async_poll())
    await hass.async_block_till_done()
    return entry, connection, valve


async def write_display(hass, enabled):
    await hass.services.async_call("switch", "turn_on" if enabled else "turn_off", {
        "entity_id": entity_id(hass, "switch", "valve-a", "display_off"),
    }, blocking=True)


@pytest.mark.parametrize("firmware,twin,clack,supported", [
    (410, False, False, False), (411, False, False, False),
    (412, False, False, False), (413, False, False, True),
    (440, False, False, True), (122, True, False, True),
    (600, False, False, True), (618, False, False, True),
    (618, False, True, True),
])
async def test_display_switch_follows_settings_ui_eligibility(
    hass, runtime_factory, bluetooth_io, transport, clock, firmware, twin, clack, supported,
):
    _, connection, valve = await setup_display(
        hass, runtime_factory, bluetooth_io, transport, clock, firmware, twin, clack,
    )
    identifier = er.async_get(hass).async_get_entity_id("switch", DOMAIN, "valve-a_display_off")
    assert bool(identifier) is supported
    if supported:
        assert er.async_get(hass).async_get(identifier).entity_category == "config"
        assert hass.states.get(identifier).state == "off"
    else:
        with pytest.raises(HomeAssistantError, match="unavailable"):
            await connection.async_set_display_off(True)
        assert valve.display_writes == []


@pytest.mark.parametrize("firmware,twin,clack", [
    (122, True, False), (413, False, False), (440, False, False),
    (618, False, False), (618, False, True),
])
@pytest.mark.parametrize("persistent", [False, True])
async def test_display_writes_confirm_both_directions_and_survive_reconnect(
    hass, runtime_factory, bluetooth_io, transport, clock, firmware, twin, clack, persistent,
):
    entry, connection, valve = await setup_display(
        hass, runtime_factory, bluetooth_io, transport, clock, firmware, twin, clack,
    )
    identifier = entity_id(hass, "switch", "valve-a", "display_off")
    if persistent:
        await connection.async_set_persistent_connection_enabled(True)
        await clock.run(connection.async_poll())
        assert valve.clients[-1].is_connected
    valve.status_bits = 0x0F
    for enabled in (True, False, True):
        await clock.run(write_display(hass, enabled))
        assert hass.states.get(identifier).state == ("on" if enabled else "off")
        assert valve.display_off is enabled
        assert valve.status_bits == 0x0F
        assert valve.clients[-1].is_connected is persistent
        if firmware < 600:
            data = connection.dashboard_data
            assert data.shutoff_setting_enabled and data.bypass_setting_enabled
            assert data.shutoff_active and data.bypass_active
            # Exact field-specific packets protect every unrelated setting.
            expected = b"v" * 13 + bytes((0x47, int(enabled))) + b"v" * 5
            assert (valve.clients[-1], expected) in valve.writes
        else:
            assert b'{"asdo":' + (b'1}' if enabled else b'0}') in valve.packet_requests
    assert [value for _, value in valve.display_writes] == [1, 0, 1]
    assert await clock.run(hass.config_entries.async_reload(entry.entry_id))
    # A new observation is needed after reload; stale HA state is not adopted.
    bluetooth_io.advertise(manufacturer_data={})
    await hass.async_block_till_done()
    restored = hass.data[DOMAIN][entry.entry_id][DATA_CONNECTION_MANAGER].get_connection("valve-a")
    await clock.run(restored.async_poll())
    assert entity_id(hass, "switch", "valve-a", "display_off") == identifier
    assert hass.states.get(identifier).state == "on"


@pytest.mark.parametrize("firmware", [440, 618])
@pytest.mark.parametrize("failure", ["rejection", "readback", "write"])
async def test_failed_write_does_not_publish_requested_state(
    hass, runtime_factory, bluetooth_io, transport, clock, firmware, failure,
):
    _, connection, valve = await setup_display(
        hass, runtime_factory, bluetooth_io, transport, clock, firmware,
    )
    identifier = entity_id(hass, "switch", "valve-a", "display_off")
    published = []
    @callback
    def record(event):
        if event.data["entity_id"] == identifier and event.data["new_state"] is not None:
            published.append(event.data["new_state"].state)
    remove = hass.bus.async_listen(EVENT_STATE_CHANGED, record)
    if failure == "rejection":
        valve.accept_display_settings = False
    elif failure == "readback":
        valve.report_display_off = False
    else:
        valve.operations["display_setting"].failure = OSError("Valve write failed")
    try:
        with pytest.raises(HomeAssistantError):
            await clock.run(write_display(hass, True))
        assert "on" not in published
        assert hass.states.get(identifier).state == "unavailable"
        assert len(valve.display_writes) == 1  # No automatic replay after uncertain adoption.
        assert not valve.clients[-1].is_connected
    finally:
        remove()


@pytest.mark.parametrize("firmware", [440, 618])
async def test_unload_cancels_pending_setting_and_prevents_late_state(
    hass, runtime_factory, bluetooth_io, transport, clock, firmware,
):
    entry, connection, valve = await setup_display(
        hass, runtime_factory, bluetooth_io, transport, clock, firmware,
    )
    gate = valve.operations["display_setting"].block()
    writing = asyncio.create_task(write_display(hass, True))
    await clock.run(gate.started.wait())
    assert await clock.run(hass.config_entries.async_unload(entry.entry_id))
    await clock.drain()
    with pytest.raises(asyncio.CancelledError):
        await writing
    assert gate.cancelled
    assert not valve.display_off
    assert not valve.clients[-1].is_connected
    gate.release.set()
    await clock.drain()
    assert not valve.display_off


@pytest.mark.parametrize("firmware", [440, 618])
async def test_setting_deadline_retains_unsettled_io_and_does_not_replay(
    hass, runtime_factory, bluetooth_io, transport, clock, firmware,
):
    _, connection, valve = await setup_display(
        hass, runtime_factory, bluetooth_io, transport, clock, firmware,
    )
    identifier = entity_id(hass, "switch", "valve-a", "display_off")
    gate = valve.operations["display_setting"].block()
    gate.ignore_cancellation = True
    with pytest.raises(HomeAssistantError):
        await clock.run(write_display(hass, True))
    assert gate.cancelled and connection.cleanup_pending
    assert hass.states.get(identifier).state != "on"
    attempts = len(transport.attempts)
    with pytest.raises(HomeAssistantError, match="cleanup"):
        await clock.run(connection.async_set_display_off(False))
    assert len(transport.attempts) == attempts
    gate.release.set()
    await clock.until(lambda: not connection.cleanup_pending)
    # Late I/O cannot republish state or trigger an automatic command replay.
    assert len(valve.display_writes) == 1
    assert hass.states.get(identifier).state != "on"
    await clock.advance(31)
    await clock.run(connection.async_poll())
    await clock.run(write_display(hass, True))
    assert hass.states.get(identifier).state == "on"
    assert len(valve.display_writes) == 2


@pytest.mark.parametrize("firmware", [440, 618])
async def test_commands_serialize_with_each_other_and_manual_refresh(
    hass, runtime_factory, bluetooth_io, transport, clock, firmware,
):
    _, connection, valve = await setup_display(
        hass, runtime_factory, bluetooth_io, transport, clock, firmware,
    )
    gate = valve.operations["display_setting"].block()
    first = asyncio.create_task(write_display(hass, True))
    await clock.run(gate.started.wait())
    second = asyncio.create_task(write_display(hass, False))
    refresh = asyncio.create_task(connection.async_refresh())
    await clock.drain()
    assert len(valve.display_writes) == 1
    assert hass.states.get(entity_id(hass, "switch", "valve-a", "display_off")).state == "off"
    gate.release.set()
    await clock.run(asyncio.gather(first, second, refresh))
    assert [value for _, value in valve.display_writes] == [1, 0]
    assert not valve.display_off
    assert hass.states.get(entity_id(hass, "switch", "valve-a", "display_off")).state == "off"


@pytest.mark.parametrize("firmware", [440, 618])
async def test_changed_pin_prevents_any_configuration_write(
    hass, runtime_factory, bluetooth_io, transport, clock, firmware,
):
    _, connection, valve = await setup_display(
        hass, runtime_factory, bluetooth_io, transport, clock, firmware,
    )
    valve.pin = "9753"
    with pytest.raises(HomeAssistantError):
        await clock.run(write_display(hass, True))
    assert valve.display_writes == []
    assert connection.authentication_lockout
    assert hass.states.get(entity_id(hass, "switch", "valve-a", "display_off")).state == "unavailable"


async def test_newer_setting_needs_explicit_readback_even_when_value_is_unchanged(
    hass, runtime_factory, bluetooth_io, transport, clock,
):
    _, connection, valve = await setup_display(
        hass, runtime_factory, bluetooth_io, transport, clock, 618,
    )
    valve.report_display_off = False
    with pytest.raises(HomeAssistantError, match="timed out"):
        await clock.run(write_display(hass, False))
    assert len(valve.display_writes) == 1


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("persistent", [False, True])
async def test_newer_old_value_in_flight_remains_pending_until_matching_readback(
    hass, runtime_factory, bluetooth_io, transport, clock, enabled, persistent,
):
    _, connection, valve = await setup_display(
        hass, runtime_factory, bluetooth_io, transport, clock, 618,
    )
    valve.display_off = not enabled
    if persistent:
        await connection.async_set_persistent_connection_enabled(True)
        await clock.run(connection.async_poll())
    gate = valve.operations["display_setting"].block()
    writing = asyncio.create_task(write_display(hass, enabled))
    await clock.run(gate.started.wait())
    # A stream update can arrive after submission but before device adoption.
    # Its old value is not a rejection of the pending command.
    for packet in frames({"asdo": int(not enabled)}):
        valve.clients[-1].notify(packet)
    await clock.drain()
    gate.release.set()
    await clock.run(writing)
    assert hass.states.get(entity_id(hass, "switch", "valve-a", "display_off")).state == (
        "on" if enabled else "off"
    )
    assert valve.display_off is enabled
    assert [value for _, value in valve.display_writes] == [int(enabled)]
    assert valve.clients[-1].is_connected is persistent


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("persistent", [False, True])
async def test_older_stale_dashboard_remains_pending_until_matching_readback(
    hass, runtime_factory, bluetooth_io, transport, clock, enabled, persistent,
):
    _, connection, valve = await setup_display(
        hass, runtime_factory, bluetooth_io, transport, clock, 440,
    )
    valve.display_off = not enabled
    if persistent:
        await connection.async_set_persistent_connection_enabled(True)
        await clock.run(connection.async_poll())
    for value in (not enabled, not enabled, enabled):
        reply = dashboard_frames()
        first = bytearray(reply[0])
        first[18] = 0x10 if value else 0
        reply[0] = bytes(first)
        valve.dashboards.append(reply)
    await clock.run(write_display(hass, enabled))
    assert hass.states.get(entity_id(hass, "switch", "valve-a", "display_off")).state == (
        "on" if enabled else "off"
    )
    assert valve.display_off is enabled
    assert [value for _, value in valve.display_writes] == [int(enabled)]
    assert valve.clients[-1].is_connected is persistent


@pytest.mark.parametrize("firmware", [440, 618])
async def test_unload_during_pending_readback_fences_late_confirmation(
    hass, runtime_factory, bluetooth_io, transport, clock, firmware,
):
    entry, connection, valve = await setup_display(
        hass, runtime_factory, bluetooth_io, transport, clock, firmware,
    )
    valve.report_display_off = False
    writing = asyncio.create_task(write_display(hass, True))
    await clock.until(lambda: len(valve.display_writes) == 1)
    await clock.drain()
    assert not writing.done() and valve.display_off
    handlers = list(valve.clients[-1].notification_history)
    assert await clock.run(hass.config_entries.async_unload(entry.entry_id))
    with pytest.raises(asyncio.CancelledError):
        await writing
    assert not connection.cleanup_pending
    assert not valve.clients[-1].is_connected
    late = frames({"asdo": 1}) if firmware >= 600 else dashboard_frames()
    if firmware < 600:
        first = bytearray(late[0])
        first[18] = 0x10
        late[0] = bytes(first)
    for handler in handlers:
        for packet in late:
            handler(NOTIFY, bytearray(packet))
    await clock.drain()
    assert hass.states.get(entity_id(hass, "switch", "valve-a", "display_off")).state == "unavailable"
    assert len(valve.display_writes) == 1


@pytest.mark.parametrize("firmware", [440, 618])
async def test_nonmatching_readback_times_out_without_replaying_the_setting(
    hass, runtime_factory, bluetooth_io, transport, clock, firmware,
):
    _, connection, valve = await setup_display(
        hass, runtime_factory, bluetooth_io, transport, clock, firmware,
    )
    valve.accept_display_settings = False
    writing = asyncio.create_task(write_display(hass, True))
    await clock.run(valve.operations["display_setting"].started.wait())
    started = clock.loop.time()
    with pytest.raises(HomeAssistantError, match="timed out"):
        await clock.run(writing)
    # Old values remain pending for the documented confirmation window;
    # neither a stream of them nor fast Dashboard replies can extend it.
    assert 8 < clock.loop.time() - started < 11
    assert hass.states.get(entity_id(hass, "switch", "valve-a", "display_off")).state == "unavailable"
    assert not valve.display_off
    assert len(valve.display_writes) == 1


@pytest.mark.parametrize("firmware", [440, 618])
async def test_external_display_change_updates_switch_without_writing(
    hass, runtime_factory, bluetooth_io, transport, clock, firmware,
):
    _, connection, valve = await setup_display(
        hass, runtime_factory, bluetooth_io, transport, clock, firmware,
    )
    valve.display_off = True
    await clock.advance(31)
    await clock.run(connection.async_poll())
    assert hass.states.get(entity_id(hass, "switch", "valve-a", "display_off")).state == "on"
    assert valve.display_writes == []
    assert not valve.clients[-1].is_connected


async def test_newer_missing_command_acknowledgement_is_not_success(
    hass, runtime_factory, bluetooth_io, transport, clock,
):
    _, connection, valve = await setup_display(
        hass, runtime_factory, bluetooth_io, transport, clock, 618,
    )
    valve.chunk_acknowledgements.append(None)
    with pytest.raises(HomeAssistantError, match="timed out"):
        await clock.run(write_display(hass, True))
    assert not valve.display_off
    assert hass.states.get(entity_id(hass, "switch", "valve-a", "display_off")).state == "unavailable"


async def test_newer_missing_or_invalid_display_is_not_an_assumed_off_value(
    hass, runtime_factory, bluetooth_io, transport, clock,
):
    _, connection, valve = await setup_display(
        hass, runtime_factory, bluetooth_io, transport, clock, 618, report=False,
    )
    identifier = entity_id(hass, "switch", "valve-a", "display_off")
    assert hass.states.get(identifier).state == "unavailable"
    for value in (None, True, -1, 2, "0", 0.5):
        valve.dashboards.append(frames({"asdo": value, "dwu": 567}))
        await clock.advance(31)
        await clock.run(connection.async_poll())
        assert hass.states.get(identifier).state == "unavailable"
    valve.dashboards.append(frames({"asdo": 0}))
    await clock.advance(31)
    await clock.run(connection.async_poll())
    assert hass.states.get(identifier).state == "off"
    assert valve.display_writes == []


@pytest.mark.parametrize("firmware", [440, 618])
async def test_expired_display_reading_disables_control_until_new_readback(
    hass, runtime_factory, bluetooth_io, transport, clock, firmware,
):
    _, connection, valve = await setup_display(
        hass, runtime_factory, bluetooth_io, transport, clock, firmware,
    )
    identifier = entity_id(hass, "switch", "valve-a", "display_off")
    await clock.advance(connection.on_demand_poll_interval + RECOVERY_ALLOWANCE + 1)
    assert hass.states.get(identifier).state == "unavailable"
    await clock.run(connection.async_poll())
    assert hass.states.get(identifier).state == "off"
    assert valve.display_writes == []


async def test_newly_identified_unsupported_firmware_disables_existing_switch(
    hass, runtime_factory, bluetooth_io, transport, clock,
):
    _, connection, valve = await setup_display(
        hass, runtime_factory, bluetooth_io, transport, clock,
    )
    identifier = entity_id(hass, "switch", "valve-a", "display_off")
    valve.firmware_version = 412
    valve.authentication_required = False
    await clock.advance(31)
    await clock.run(connection.async_poll())
    assert hass.states.get(identifier).state == "unavailable"
    with pytest.raises(HomeAssistantError, match="unavailable"):
        await connection.async_set_display_off(True)
    assert valve.display_writes == []
    assert entity_id(hass, "switch", "valve-a", "display_off") == identifier


@pytest.mark.parametrize("firmware", [440, 618])
async def test_display_command_completes_with_automatic_monitoring_running(
    hass, runtime_factory, bluetooth_io, transport, clock, firmware,
):
    _, connection, valve = await setup_display(
        hass, runtime_factory, bluetooth_io, transport, clock, firmware,
    )
    await hass.async_start()
    await clock.run(write_display(hass, True))
    assert hass.states.get(entity_id(hass, "switch", "valve-a", "display_off")).state == "on"
    assert len(valve.display_writes) == 1
    await clock.advance(connection.on_demand_poll_interval + 1)
    await clock.until(lambda: len(valve.clients) >= 3 and not valve.clients[-1].is_connected)
    assert len(valve.display_writes) == 1
