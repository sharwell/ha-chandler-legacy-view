"""Actual HA refresh actions and automatic deadlines share valve ownership."""

import asyncio
import math
from pathlib import Path
import re

import pytest
from homeassistant.components import homeassistant as core_component
from homeassistant.exceptions import HomeAssistantError

from custom_components.chandler_legacy_view.preferences import (
    INHERIT_ON_DEMAND_INTERVAL, ON_DEMAND_INTERVAL, VALVE_PREFERENCES,
)
from evb034_packets import frames
from packets import dashboard_frames
from test_configured_authentication import entity_id, manager, update_options
from test_evb034_authentication import setup_valve


@pytest.fixture
async def refresh_action(hass):
    # Install HA's real core action rather than a test imitation of dispatch.
    await core_component.async_setup(hass, {})
    async def refresh(*identifiers):
        await hass.services.async_call("homeassistant", "update_entity", {
            "entity_id": list(identifiers),
        }, blocking=True)
    return refresh


async def setup_readings(hass, runtime_factory, bluetooth_io):
    entry = await runtime_factory()
    bluetooth_io.advertise()
    await hass.async_block_till_done()
    return entry, manager(hass, entry).get_connection("valve-a"), entity_id(hass, "sensor", "valve-a", "water_usage_today")


async def test_frequent_advertisements_respect_fifteen_minutes_and_due_timer(
    hass, runtime_factory, bluetooth_io, transport, clock,
):
    entry, connection, usage = await setup_readings(hass, runtime_factory, bluetooth_io)
    valve = transport.valve()
    valve.firmware_version = 440
    valve.dashboards.extend([dashboard_frames(gallons=111), dashboard_frames(gallons=987)])
    await clock.run(hass.async_start())
    await clock.until(lambda: valve.clients and not valve.clients[-1].is_connected)
    assert hass.states.get(usage).state == "111"
    for _ in range(28):
        await clock.advance(31)
        bluetooth_io.advertise()
        await clock.drain()
        assert len(valve.clients) == 1
    await clock.advance(30)
    assert len(valve.clients) == 1
    # No advertisement is required at the actual successful-read deadline.
    await clock.until(lambda: hass.states.get(usage).state == "987" and not valve.clients[-1].is_connected, budget=5)
    assert len(valve.clients) == 2


async def test_manual_multi_entity_refresh_coalesces_and_obeys_cooldown(
    hass, runtime_factory, bluetooth_io, transport, clock, refresh_action,
):
    _, connection, usage = await setup_readings(hass, runtime_factory, bluetooth_io)
    await clock.run(refresh_action(usage))
    valve = transport.valve()
    success = connection.last_success
    valve.dashboards.append(dashboard_frames(gallons=987))
    gate = valve.operations["dashboard"].block()
    gate.started.clear()
    flow = entity_id(hass, "sensor", "valve-a", "present_flow")
    first = asyncio.create_task(refresh_action(usage, flow))
    second = asyncio.create_task(refresh_action(flow))
    await clock.drain()
    assert len(valve.clients) == 1
    assert not first.done() and not second.done()
    await clock.run(gate.started.wait())
    assert len(valve.clients) == 2
    assert valve.dashboard_requests == 2
    gate.release.set()
    await clock.run(asyncio.gather(first, second))
    assert hass.states.get(usage).state == "987"
    assert connection.last_success > success
    assert all(not client.is_connected for client in valve.clients)


async def test_manual_refresh_joins_automatic_first_read(
    hass, runtime_factory, bluetooth_io, transport, clock, refresh_action,
):
    _, connection, usage = await setup_readings(hass, runtime_factory, bluetooth_io)
    valve = transport.valve()
    gate = valve.operations["device_list"].block()
    await clock.run(hass.async_start())
    await clock.run(gate.started.wait())
    manual = asyncio.create_task(refresh_action(usage))
    await clock.drain()
    assert not manual.done()
    gate.release.set()
    await clock.run(manual)
    assert connection.last_success is not None
    assert len(valve.clients) == 1


@pytest.mark.parametrize("failure", ["unavailable", "lockout", "read"])
async def test_update_entity_reports_failure_without_changing_success(
    hass, runtime_factory, bluetooth_io, transport, clock, refresh_action, failure,
):
    _, connection, usage = await setup_readings(hass, runtime_factory, bluetooth_io)
    if failure == "unavailable":
        bluetooth_io.expire()
    elif failure == "lockout":
        connection.set_authentication_lockout(True)
    else:
        transport.valve().operations["dashboard"].failure = OSError("link lost")
    with pytest.raises(HomeAssistantError):
        await clock.run(refresh_action(usage))
    assert connection.last_success is None
    assert connection.dashboard_data is None
    assert len(transport.attempts) == (1 if failure == "read" else 0)


async def test_manual_refresh_uses_live_persistent_evb019_session(
    hass, runtime_factory, bluetooth_io, transport, clock, refresh_action,
):
    _, connection, usage = await setup_readings(hass, runtime_factory, bluetooth_io)
    await connection.async_set_persistent_connection_enabled(True)
    await clock.run(connection.async_poll())
    valve = transport.valve()
    valve.dashboards.append(dashboard_frames(gallons=987))
    await clock.run(refresh_action(usage))
    assert hass.states.get(usage).state == "987"
    assert len(valve.clients) == 1
    assert valve.clients[0].is_connected
    assert valve.dashboard_requests == 2


async def test_buffered_evb034_refresh_keeps_original_confirmation_time(
    hass, runtime_factory, bluetooth_io, transport, clock, refresh_action,
):
    _, connection, valve, _ = await setup_valve(hass, runtime_factory, bluetooth_io, transport)
    await connection.async_set_persistent_poll_interval(300)
    await connection.async_set_persistent_connection_enabled(True)
    await clock.run(connection.async_poll())
    valve.stream_enabled = False
    await clock.drain()
    owner = valve.clients[0]
    for frame in frames({"dwu": 98700}):
        owner.notify(frame)
    received = clock.utcnow()
    await clock.advance(20)
    usage = entity_id(hass, "sensor", "valve-a", "water_usage_today")
    await clock.run(refresh_action(usage))
    assert float(hass.states.get(usage).state) == 987
    assert abs((connection.last_success - received).total_seconds()) < 0.1
    assert (clock.utcnow() - connection.last_success).total_seconds() >= 20
    assert len(valve.clients) == 1 and owner.is_connected
    assert valve.packet_requests == []
    assert all(payload not in (b"u" * 20, b"t" * 20) for _, payload in valve.writes)


async def test_ha_disable_polling_blocks_all_automatic_triggers_but_not_refresh(
    hass, runtime_factory, bluetooth_io, transport, clock, refresh_action,
):
    entry, connection, usage = await setup_readings(hass, runtime_factory, bluetooth_io)
    hass.config_entries.async_update_entry(entry, pref_disable_polling=True)
    await hass.async_block_till_done()
    await clock.run(hass.async_start())
    for _ in range(4):
        bluetooth_io.advertise()
        await clock.advance(901)
    assert transport.attempts == []
    await clock.run(refresh_action(usage))
    assert len(transport.valve().clients) == 1
    assert connection.last_success is not None
    hass.config_entries.async_update_entry(entry, pref_disable_polling=False)
    await hass.async_block_till_done()
    await clock.until(lambda: len(transport.valve().clients) == 2, budget=901)


async def test_options_inherit_override_clear_and_independent_due_times(
    hass, runtime_factory, bluetooth_io, transport, clock,
):
    entry, connection, _ = await setup_readings(hass, runtime_factory, bluetooth_io)
    bluetooth_io.advertise("valve-b")
    await hass.async_block_till_done()
    await update_options(hass, entry, {ON_DEMAND_INTERVAL: 2})
    await update_options(hass, entry, {"device_address": "valve-a", "device_on_demand_interval": 1})
    await clock.run(hass.async_start())
    await clock.until(lambda: all(transport.valve(a).clients and not transport.valve(a).clients[-1].is_connected for a in ("valve-a", "valve-b")))
    await clock.advance(59)
    assert [len(transport.valve(a).clients) for a in ("valve-a", "valve-b")] == [1, 1]
    await clock.until(lambda: len(transport.valve().clients) == 2, budget=2)
    assert len(transport.valve("valve-b").clients) == 1
    await clock.until(lambda: not transport.valve().clients[-1].is_connected)
    await update_options(hass, entry, {"device_address": "valve-a", INHERIT_ON_DEMAND_INTERVAL: True})
    assert ON_DEMAND_INTERVAL not in entry.options[VALVE_PREFERENCES]["valve-a"]
    assert connection.on_demand_poll_interval == 120


async def test_documented_automation_and_script_execute_and_have_related_entities(
    hass, runtime_factory, bluetooth_io, transport, clock, refresh_action,
):
    from homeassistant.components import automation, script
    from homeassistant.helpers import condition, template, trigger
    from homeassistant.setup import async_setup_component
    from homeassistant.util import yaml as yaml_util

    entry, connection, usage = await setup_readings(hass, runtime_factory, bluetooth_io)
    hass.config_entries.async_update_entry(entry, pref_disable_polling=True)
    await hass.async_block_till_done()
    template.async_setup(hass)
    await condition.async_setup(hass)
    await trigger.async_setup(hass)
    document = (Path(__file__).resolve().parents[1] / "docs" / "polling.md").read_text(encoding="utf-8")
    examples = re.findall(r"```yaml\n(.*?)```", document, re.DOTALL)
    assert len(examples) == 2
    for example in examples:
        configuration = yaml_util.parse_yaml(example.replace("sensor.water_softener_water_usage_today", usage))
        domain = next(iter(configuration))
        assert await async_setup_component(hass, domain, configuration)
    await hass.async_block_till_done()
    assert automation.automations_with_entity(hass, usage) == ["automation.poll_chandler_readings_hourly"]
    assert script.scripts_with_entity(hass, usage) == ["script.refresh_chandler_readings"]
    transport.valve().dashboards.extend([dashboard_frames(gallons=111), dashboard_frames(gallons=987)])
    await clock.run(hass.services.async_call("script", "refresh_chandler_readings", {}, blocking=True))
    assert hass.states.get(usage).state == "111"
    await clock.run(hass.services.async_call("automation", "trigger", {
        "entity_id": "automation.poll_chandler_readings_hourly",
    }, blocking=True))
    assert hass.states.get(usage).state == "987"
    assert len(transport.valve().clients) == 2


async def test_cancelled_refresh_caller_does_not_abandon_work_and_unload_stops_it(
    hass, runtime_factory, bluetooth_io, transport, clock, refresh_action,
):
    entry, connection, usage = await setup_readings(hass, runtime_factory, bluetooth_io)
    gate = transport.valve().operations["device_list"].block()
    caller = asyncio.create_task(refresh_action(usage))
    await clock.run(gate.started.wait())
    caller.cancel()
    result = await asyncio.gather(caller, return_exceptions=True)
    assert isinstance(result[0], asyncio.CancelledError)
    assert not gate.cancelled
    assert transport.valve().clients[0].is_connected
    assert await clock.run(hass.config_entries.async_unload(entry.entry_id))
    assert gate.cancelled
    assert not transport.valve().clients[0].is_connected
    assert connection.last_success is None


async def test_persistent_manual_and_scheduled_read_join_one_dashboard_request(
    hass, runtime_factory, bluetooth_io, transport, clock, refresh_action,
):
    _, connection, usage = await setup_readings(hass, runtime_factory, bluetooth_io)
    await connection.async_set_persistent_connection_enabled(True)
    await clock.run(connection.async_poll())
    valve = transport.valve()
    valve.dashboards.append(dashboard_frames(gallons=987))
    gate = valve.operations["dashboard"].block()
    gate.started.clear()
    await clock.run(gate.started.wait())
    manual = asyncio.create_task(refresh_action(usage))
    await clock.drain()
    assert valve.dashboard_requests == 2
    gate.release.set()
    await clock.run(manual)
    assert hass.states.get(usage).state == "987"
    assert valve.dashboard_requests == 2
    assert len(valve.clients) == 1


@pytest.mark.parametrize("interval", [0, 1441, float("nan"), float("inf")])
async def test_invalid_on_demand_option_does_not_change_saved_interval(
    hass, runtime_factory, bluetooth_io, interval,
):
    from homeassistant.data_entry_flow import FlowResultType, InvalidData
    entry, connection, _ = await setup_readings(hass, runtime_factory, bluetooth_io)
    original = dict(entry.options)
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    if math.isnan(interval):
        result = await hass.config_entries.options.async_configure(flow["flow_id"], user_input={ON_DEMAND_INTERVAL: interval})
        assert result["type"] is FlowResultType.FORM
        assert result["errors"][ON_DEMAND_INTERVAL] == "invalid_on_demand_interval"
    else:
        # HA's real selector rejects these before calling the options handler.
        with pytest.raises(InvalidData):
            await hass.config_entries.options.async_configure(flow["flow_id"], user_input={ON_DEMAND_INTERVAL: interval})
    assert dict(entry.options) == original
    assert connection.on_demand_poll_interval == 900
