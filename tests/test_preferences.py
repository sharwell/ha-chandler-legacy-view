"""Local controls and options share durable per-valve monitoring preferences."""

import math

import pytest
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from custom_components import chandler_legacy_view as integration
from custom_components.chandler_legacy_view.const import DATA_CONNECTION_MANAGER, DOMAIN
from custom_components.chandler_legacy_view.preferences import (
    INHERIT_PERSISTENT_INTERVAL, PERSISTENT_ENABLED, PERSISTENT_INTERVAL,
    PREFERENCES_VERSION, VALVE_PREFERENCES,
)
from packets import dashboard_frames
from test_configured_authentication import entity_id, manager, update_options


async def number_write(hass, address, value):
    await hass.services.async_call("number", "set_value", {
        "entity_id": entity_id(hass, "number", address, "persistent_poll_interval"),
        "value": value,
    }, blocking=True)
    await hass.async_block_till_done()


async def test_number_writes_defaults_overrides_and_offline_options_share_one_value(
    hass, runtime_factory, bluetooth_io, transport,
):
    entry = await runtime_factory()
    for address in ("valve-a", "valve-b"):
        bluetooth_io.advertise(address)
    await hass.async_block_till_done()
    original = manager(hass, entry)
    number = entity_id(hass, "number", "valve-a", "persistent_poll_interval")
    record = er.async_get(hass).async_get(number)
    await number_write(hass, "valve-a", 10)
    assert entry.options[VALVE_PREFERENCES]["valve-a"][PERSISTENT_INTERVAL] == 10
    await update_options(hass, entry, {PERSISTENT_INTERVAL: 20})
    assert manager(hass, entry) is original
    assert float(hass.states.get(number).state) == 10
    assert original.get_connection("valve-b").persistent_poll_interval == 20
    bluetooth_io.expire()
    await hass.async_block_till_done()
    assert hass.states.get(number).state != "unavailable"
    await number_write(hass, "valve-a", 180)
    await number_write(hass, "valve-a", 180)
    assert original.get_connection("valve-a").persistent_poll_interval == 180
    await update_options(hass, entry, {"device_address": "valve-a", "device_persistent_interval": 90})
    assert float(hass.states.get(number).state) == 90
    await update_options(hass, entry, {"device_address": "valve-a", INHERIT_PERSISTENT_INTERVAL: True})
    assert PERSISTENT_INTERVAL not in entry.options[VALVE_PREFERENCES]["valve-a"]
    assert float(hass.states.get(number).state) == 20
    assert er.async_get(hass).async_get(number) == record
    assert transport.attempts == []


async def test_restored_mode_and_interval_govern_real_session_and_survive_cleanup(
    hass, runtime_factory, bluetooth_io, transport, clock,
):
    entry = await runtime_factory()
    bluetooth_io.advertise()
    await hass.async_block_till_done()
    switch = entity_id(hass, "switch", "valve-a", "persistent_connection")
    usage = entity_id(hass, "sensor", "valve-a", "water_usage_today")
    await number_write(hass, "valve-a", 180)
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
    assert await clock.run(hass.config_entries.async_reload(entry.entry_id))
    connection = manager(hass, entry).get_connection("valve-a")
    assert hass.states.get(switch).state == "on"
    assert connection.persistent_poll_interval == 180
    valve = transport.valve()
    valve.dashboards.extend([dashboard_frames(gallons=111), dashboard_frames(gallons=987)])
    await clock.run(connection.async_poll())
    assert valve.clients[0].is_connected
    await clock.advance(179)
    assert hass.states.get(usage).state == "111"
    await clock.until(lambda: hass.states.get(usage).state == "987", budget=5)
    assert len(valve.clients) == 1
    original = manager(hass, entry)
    await update_options(hass, entry, {"device_address": "valve-a", "device_persistent_interval": 90})
    assert manager(hass, entry) is original
    assert len(valve.clients) == 1 and valve.clients[0].is_connected
    valve.dashboards.append(dashboard_frames(gallons=333))
    await clock.until(lambda: hass.states.get(usage).state == "333", budget=91)
    assert await clock.run(hass.config_entries.async_unload(entry.entry_id))
    assert entry.options[VALVE_PREFERENCES]["valve-a"][PERSISTENT_ENABLED] is True
    assert not valve.clients[0].is_connected


@pytest.mark.parametrize("disabled", [False, True])
@pytest.mark.parametrize("enabled", [False, True])
async def test_real_storage_restores_before_first_poll_even_with_disabled_controls(
    hass, hass_factory, runtime_factory, bluetooth_io, transport, clock, disabled, enabled,
):
    entry = await runtime_factory()
    bluetooth_io.advertise()
    await hass.async_block_till_done()
    await number_write(hass, "valve-a", 180)
    switch = entity_id(hass, "switch", "valve-a", "persistent_connection")
    number = entity_id(hass, "number", "valve-a", "persistent_poll_interval")
    await hass.services.async_call("switch", "turn_on" if enabled else "turn_off", {"entity_id": switch}, blocking=True)
    assert await clock.run(hass.config_entries.async_unload(entry.entry_id))
    if disabled:
        for identifier in (switch, number):
            er.async_get(hass).async_update_entity(identifier, disabled_by=er.RegistryEntryDisabler.USER)
    await hass.async_stop(force=True)
    restored = await hass_factory(hass.config.config_dir)
    saved_entry = restored.config_entries.async_get_entry(entry.entry_id)
    assert saved_entry is not None
    await integration.async_setup(restored, {})
    restored.config.components.add(DOMAIN)
    await saved_entry.async_setup_locked(restored)
    connection = restored.data[DOMAIN][entry.entry_id][DATA_CONNECTION_MANAGER].get_connection("valve-a")
    assert connection.persistent_connection_enabled is enabled
    assert connection.persistent_poll_interval == 180
    if disabled:
        assert restored.states.get(switch) is None
        assert restored.states.get(number) is None
    else:
        assert restored.states.get(switch).state == ("on" if enabled else "off")
        assert float(restored.states.get(number).state) == 180
    await clock.run(connection.async_poll())
    assert transport.valve().clients[-1].is_connected is enabled
    assert await clock.run(restored.config_entries.async_unload(entry.entry_id))
    assert saved_entry.options[VALVE_PREFERENCES]["valve-a"][PERSISTENT_ENABLED] is enabled


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, 4, 301])
async def test_invalid_local_interval_does_not_change_saved_configuration(
    hass, runtime_factory, bluetooth_io, value,
):
    entry = await runtime_factory()
    bluetooth_io.advertise()
    await hass.async_block_till_done()
    options = dict(entry.options)
    with pytest.raises((HomeAssistantError, ValueError)):
        await number_write(hass, "valve-a", value)
    assert dict(entry.options) == options
    assert manager(hass, entry).get_connection("valve-a").persistent_poll_interval == 10


@pytest.mark.parametrize("value", [None, "obsolete", float("nan"), -1, 301])
async def test_invalid_stored_values_fall_back_without_becoming_user_choices(
    hass, entry_factory, runtime_factory, bluetooth_io, value,
):
    entry = entry_factory(options={PERSISTENT_INTERVAL: 20, VALVE_PREFERENCES: {
        "valve-a": {PERSISTENT_INTERVAL: value, PERSISTENT_ENABLED: "on"},
        "valve-b": {PERSISTENT_ENABLED: False},
    }})
    await runtime_factory(entry=entry)
    for address in ("valve-a", "valve-b"):
        bluetooth_io.advertise(address)
    await hass.async_block_till_done()
    for address in ("valve-a", "valve-b"):
        connection = manager(hass, entry).get_connection(address)
        assert not connection.persistent_connection_enabled
        assert connection.persistent_poll_interval == 20
    assert entry.options[VALVE_PREFERENCES]["valve-b"][PERSISTENT_ENABLED] is False


@pytest.mark.parametrize("historical", ["180", "unknown", "unavailable", "10"])
async def test_migration_imports_only_recoverable_history_once(
    hass, entry_factory, runtime_factory, bluetooth_io, clock, historical,
):
    entry = entry_factory()
    registry = er.async_get(hass)
    number = registry.async_get_or_create("number", DOMAIN, "valve-a_persistent_poll_interval", config_entry=entry)
    # Existing live HA state models an upgrade with a recoverable old value,
    # rather than injecting an initialized production connection's defaults.
    hass.states.async_set(number.entity_id, historical)
    await runtime_factory(entry=entry)
    bluetooth_io.advertise()
    await hass.async_block_till_done()
    saved = entry.options.get(VALVE_PREFERENCES, {}).get("valve-a", {})
    assert (PERSISTENT_INTERVAL in saved) is (historical == "180")
    assert entry.options[PREFERENCES_VERSION] == 1
    await update_options(hass, entry, {"device_address": "valve-a", INHERIT_PERSISTENT_INTERVAL: True, PERSISTENT_INTERVAL: 30})
    assert await clock.run(hass.config_entries.async_reload(entry.entry_id))
    assert PERSISTENT_INTERVAL not in entry.options[VALVE_PREFERENCES]["valve-a"]
    assert manager(hass, entry).get_connection("valve-a").persistent_poll_interval == 30


async def test_authoritative_options_win_over_historical_states(
    hass, entry_factory, runtime_factory, bluetooth_io,
):
    entry = entry_factory(options={PERSISTENT_INTERVAL: 20, VALVE_PREFERENCES: {
        "valve-a": {PERSISTENT_ENABLED: False, PERSISTENT_INTERVAL: 40},
    }})
    registry = er.async_get(hass)
    for domain, suffix, state in (("switch", PERSISTENT_ENABLED, "on"), ("number", PERSISTENT_INTERVAL, "180")):
        record = registry.async_get_or_create(domain, DOMAIN, "valve-a_" + suffix, config_entry=entry)
        hass.states.async_set(record.entity_id, state)
    await runtime_factory(entry=entry)
    bluetooth_io.advertise()
    await hass.async_block_till_done()
    connection = manager(hass, entry).get_connection("valve-a")
    assert not connection.persistent_connection_enabled
    assert connection.persistent_poll_interval == 40


async def test_invalid_options_leave_preferences_and_credentials_unchanged(
    hass, runtime_factory, bluetooth_io,
):
    entry = await runtime_factory()
    bluetooth_io.advertise()
    await hass.async_block_till_done()
    original = dict(entry.options), dict(entry.data)
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(flow["flow_id"], user_input={
        PERSISTENT_INTERVAL: float("nan"), "default_passcode": "9753",
    })
    assert result["type"] is FlowResultType.FORM
    assert result["errors"][PERSISTENT_INTERVAL] == "invalid_interval"
    assert (dict(entry.options), dict(entry.data)) == original
