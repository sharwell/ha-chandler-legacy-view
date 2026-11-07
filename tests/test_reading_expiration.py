"""Published expiration/recovery uses confirmed readings, never radio presence."""

import asyncio
import logging

import pytest
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from custom_components.chandler_legacy_view.preferences import ON_DEMAND_INTERVAL
from packets import dashboard_frames
from test_configured_authentication import entity_id, manager, update_options
from test_polling_policy import refresh_action, setup_readings


async def age_to(clock, connection, seconds):
    await clock.advance(max(0, seconds - (clock.utcnow() - connection.last_success).total_seconds()))


async def test_absolute_deadline_and_atomic_fresh_value_recovery_with_stable_entity(
    hass, runtime_factory, bluetooth_io, transport, clock, refresh_action, caplog,
):
    caplog.set_level(logging.DEBUG, logger="custom_components.chandler_legacy_view.freshness")
    entry, connection, usage = await setup_readings(hass, runtime_factory, bluetooth_io)
    hass.config_entries.async_update_entry(entry, pref_disable_polling=True)
    valve = transport.valve()
    valve.dashboards.append(dashboard_frames(gallons=123))
    await clock.run(refresh_action(usage))
    saved_record = er.async_get(hass).async_get(usage)
    success = connection.last_success
    await clock.run(hass.async_start())
    await age_to(clock, connection, 1199)
    bluetooth_io.advertise()
    await clock.drain()
    assert hass.states.get(usage).state == "123"
    published = []
    @callback
    def record(event):
        if event.data["entity_id"] == usage:
            published.append(event.data["new_state"].state)
    remove = hass.bus.async_listen(EVENT_STATE_CHANGED, record)
    try:
        await age_to(clock, connection, 1200.1)
        assert hass.states.get(usage).state == "unavailable"
        assert connection.last_success == success
        presence = er.async_get(hass).async_get_entity_id("binary_sensor", "chandler_legacy_view", "valve-a")
        assert hass.states.get(presence).state == "on"
        await update_options(hass, entry, {ON_DEMAND_INTERVAL: 60})
        bluetooth_io.advertise()
        await clock.advance(30)
        assert hass.states.get(usage).state == "unavailable"
        valve.dashboards.append(dashboard_frames(gallons=987))
        await clock.run(refresh_action(usage))
        assert published == ["unavailable", "987"]
        assert er.async_get(hass).async_get(usage) == saved_record
        assert caplog.text.count("reading water_usage_today expired:") == 1
        assert "reading water_usage_today recovered" in caplog.text
    finally:
        remove()


async def test_due_read_and_one_retry_expire_earlier_without_freshness_extension(
    hass, runtime_factory, bluetooth_io, transport, clock,
):
    entry, connection, usage = await setup_readings(hass, runtime_factory, bluetooth_io)
    await update_options(hass, entry, {ON_DEMAND_INTERVAL: 1})
    valve = transport.valve()
    valve.dashboards.append(dashboard_frames(gallons=123))
    await clock.run(connection.async_poll())
    success = connection.last_success
    valve.operations["dashboard"].failure = OSError("read failed while advertising")
    await clock.run(hass.async_start())
    for _ in range(3):
        await clock.advance(20)
        bluetooth_io.advertise()
    await clock.until(lambda: hass.states.get(usage).state == "unavailable", budget=35)
    assert valve.dashboard_requests == 3
    assert connection.last_success == success
    # Conclusive failure takes effect before the absolute six-minute age
    # limit; it does not consume the remaining grace as though data arrived.
    assert (clock.utcnow() - success).total_seconds() < 360
    assert len(valve.clients) == 3


@pytest.mark.parametrize("invalid", ["missing", "malformed", "transport"])
async def test_failed_early_manual_reads_do_not_expire_or_force_automatic_retries(
    hass, runtime_factory, bluetooth_io, transport, clock, refresh_action, invalid,
):
    _, connection, usage = await setup_readings(hass, runtime_factory, bluetooth_io)
    valve = transport.valve()
    valve.dashboards.append(dashboard_frames(gallons=123))
    await clock.run(refresh_action(usage))
    success = connection.last_success
    await clock.run(hass.async_start())
    for _ in range(2):
        if invalid == "transport":
            valve.operations["dashboard"].failure = OSError("early manual failure")
        else:
            reply = dashboard_frames(gallons=987)
            valve.dashboards.append(reply[:3] if invalid == "missing" else [*reply[:3], b"invalid", *reply[4:]])
        with pytest.raises(HomeAssistantError):
            await clock.run(refresh_action(usage))
        assert hass.states.get(usage).state == "123"
        assert connection.last_success == success
    await clock.advance(31)
    bluetooth_io.advertise()
    await clock.drain()
    assert len(valve.clients) == 3
    assert hass.states.get(usage).state == "123"


async def test_mode_and_interval_changes_recalculate_without_reviving_expired_values(
    hass, runtime_factory, bluetooth_io, transport, clock, refresh_action,
):
    entry, connection, usage = await setup_readings(hass, runtime_factory, bluetooth_io)
    hass.config_entries.async_update_entry(entry, pref_disable_polling=True)
    await clock.run(refresh_action(usage))
    await age_to(clock, connection, 100)
    await update_options(hass, entry, {ON_DEMAND_INTERVAL: 1})
    await connection.async_set_persistent_connection_enabled(True)
    await connection.async_set_persistent_poll_interval(5)
    await age_to(clock, connection, 119)
    assert hass.states.get(usage).state != "unavailable"
    await age_to(clock, connection, 120.1)
    assert hass.states.get(usage).state == "unavailable"
    await connection.async_set_persistent_poll_interval(300)
    assert hass.states.get(usage).state == "unavailable"
    transport.valve().dashboards.append(dashboard_frames(gallons=987))
    await clock.run(refresh_action(usage))
    assert hass.states.get(usage).state == "987"


async def test_no_confirmation_expires_after_startup_recovery_allowance(
    hass, runtime_factory, bluetooth_io, transport, clock, refresh_action,
):
    entry, connection, usage = await setup_readings(hass, runtime_factory, bluetooth_io)
    hass.config_entries.async_update_entry(entry, pref_disable_polling=True)
    await clock.run(hass.async_start())
    await clock.advance(299)
    assert hass.states.get(usage).state == "unknown"
    await clock.advance(1.1)
    assert hass.states.get(usage).state == "unavailable"
    assert transport.attempts == []
    transport.valve().dashboards.append(dashboard_frames(gallons=0))
    await clock.run(refresh_action(usage))
    assert hass.states.get(usage).state == "0"


async def test_first_failed_acquisition_and_retry_publish_unavailable(
    hass, runtime_factory, bluetooth_io, transport, clock,
):
    _, connection, usage = await setup_readings(hass, runtime_factory, bluetooth_io)
    transport.valve().operations["dashboard"].failure = OSError("no first reading")
    assert hass.states.get(usage).state == "unknown"
    await clock.run(hass.async_start())
    await clock.until(lambda: hass.states.get(usage).state == "unavailable", budget=40)
    assert transport.valve().dashboard_requests == 2
    assert connection.last_success is None


async def test_healthy_persistent_maximum_interval_confirms_unchanged_zero(
    hass, runtime_factory, bluetooth_io, transport, clock,
):
    _, connection, usage = await setup_readings(hass, runtime_factory, bluetooth_io)
    await connection.async_set_persistent_poll_interval(300)
    await connection.async_set_persistent_connection_enabled(True)
    valve = transport.valve()
    valve.dashboards.extend(dashboard_frames(gallons=0) for _ in range(5))
    await clock.run(connection.async_poll())
    success = connection.last_success
    for _ in range(3):
        await clock.until(lambda: connection.last_success > success, budget=301)
        assert hass.states.get(usage).state == "0"
        success = connection.last_success
    assert len(valve.clients) == 1
    assert valve.clients[0].is_connected


async def test_independent_valve_expiration_uses_its_configured_interval(
    hass, runtime_factory, bluetooth_io, transport, clock,
):
    entry, first, usage = await setup_readings(hass, runtime_factory, bluetooth_io)
    bluetooth_io.advertise("valve-b")
    await hass.async_block_till_done()
    hass.config_entries.async_update_entry(entry, pref_disable_polling=True)
    await update_options(hass, entry, {ON_DEMAND_INTERVAL: 1, "device_address": "valve-b", "device_on_demand_interval": 60})
    second = manager(hass, entry).get_connection("valve-b")
    await clock.run(first.async_poll())
    await clock.run(second.async_poll())
    await age_to(clock, first, 360.1)
    assert hass.states.get(usage).state == "unavailable"
    other = entity_id(hass, "sensor", "valve-b", "water_usage_today")
    assert hass.states.get(other).state != "unavailable"


async def test_long_custom_interval_allows_quiet_period_then_expires(
    hass, runtime_factory, bluetooth_io, transport, clock,
):
    entry, connection, usage = await setup_readings(hass, runtime_factory, bluetooth_io)
    hass.config_entries.async_update_entry(entry, pref_disable_polling=True)
    await update_options(hass, entry, {ON_DEMAND_INTERVAL: 60})
    await clock.run(connection.async_poll())
    await age_to(clock, connection, 3899)
    bluetooth_io.advertise()
    await clock.drain()
    assert hass.states.get(usage).state != "unavailable"
    await age_to(clock, connection, 3900.1)
    assert hass.states.get(usage).state == "unavailable"
    assert len(transport.valve().clients) == 1


async def test_expiration_timer_runs_when_old_backend_work_remains_quarantined(
    hass, runtime_factory, bluetooth_io, transport, clock,
):
    _, connection, usage = await setup_readings(hass, runtime_factory, bluetooth_io)
    await connection.async_set_persistent_connection_enabled(True)
    await clock.run(connection.async_poll())
    valve = transport.valve()
    gate = valve.operations["dashboard"].block()
    gate.started.clear()
    gate.ignore_cancellation = True
    await clock.run(gate.started.wait())
    await clock.until(lambda: connection.cleanup_pending)
    await age_to(clock, connection, 119)
    bluetooth_io.advertise()
    assert hass.states.get(usage).state != "unavailable"
    await age_to(clock, connection, 120.1)
    assert hass.states.get(usage).state == "unavailable"
    assert transport.attempts == ["valve-a"]
    gate.release.set()
    await clock.until(lambda: not connection.cleanup_pending)


async def test_fresh_recovery_preserves_apk_capability_unavailability(
    hass, runtime_factory, bluetooth_io, transport, clock,
):
    from packets import device_list_frame
    from test_entity_capabilities import advertise, existing_dashboard_entities, connection as get_connection

    entry, identifiers = await existing_dashboard_entities(hass, runtime_factory, bluetooth_io)
    hass.config_entries.async_update_entry(entry, pref_disable_polling=True)
    advertise(bluetooth_io, valve_type=4)
    await hass.async_block_till_done()
    connection = get_connection(hass, entry)
    valve = transport.valve()
    response = bytearray(device_list_frame(status=128, firmware=422))
    response[3] = 4
    valve.device_lists.append([bytes(response)])
    await clock.run(connection.async_poll())
    assert hass.states.get(identifiers["water_hardness"]).state == "unavailable"
    await age_to(clock, connection, 1200.1)
    assert hass.states.get(identifiers["water_usage_today"]).state == "unavailable"
    valve.device_lists.append([bytes(response)])
    valve.dashboards.append(dashboard_frames(gallons=987))
    await clock.run(connection.async_poll())
    assert hass.states.get(identifiers["water_usage_today"]).state == "987"
    assert hass.states.get(identifiers["water_hardness"]).state == "unavailable"
    assert hass.states.get(identifiers["soft_water_remaining"]).state == "unavailable"
