"""Configured PINs must reach real manager-owned sessions and published states."""

import pytest
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er

from custom_components.chandler_legacy_view.const import (
    CONF_DEFAULT_PASSCODE,
    CONF_DEVICE_ADDRESS,
    CONF_DEVICE_PASSCODE,
    CONF_DEVICE_PASSCODES,
    DATA_CONNECTION_MANAGER,
    DOMAIN,
)
from packets import dashboard_frames


def manager(hass, entry):
    return hass.data[DOMAIN][entry.entry_id][DATA_CONNECTION_MANAGER]


def entity_id(hass, domain, address, suffix):
    identifier = er.async_get(hass).async_get_entity_id(domain, DOMAIN, f"{address}_{suffix}")
    assert identifier is not None
    return identifier


async def update_options(hass, entry, user_input):
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    assert flow["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(flow["flow_id"], user_input=user_input)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()


async def test_custom_default_pin_and_changed_default_survive_reload(hass, entry_factory, runtime_factory, bluetooth_io, transport, clock):
    entry = entry_factory(data={CONF_DEFAULT_PASSCODE: "1357"})
    await runtime_factory(entry=entry)
    bluetooth_io.advertise()
    await hass.async_block_till_done()
    # The simulated valve's PIN is independent of the integration settings.
    valve = transport.valve()
    valve.pin = "1357"
    valve.dashboards.append(dashboard_frames(gallons=135))
    await clock.run(manager(hass, entry).get_connection("valve-a").async_poll())
    usage = entity_id(hass, "sensor", "valve-a", "water_usage_today")
    lockout = entity_id(hass, "switch", "valve-a", "authentication_lockout")
    assert hass.states.get(usage).state == "135"
    assert hass.states.get(lockout).state == "off"

    original_manager = manager(hass, entry)
    valve.pin = "9753"
    valve.dashboards.append(dashboard_frames(gallons=975))
    await update_options(hass, entry, {CONF_DEFAULT_PASSCODE: "9753"})
    assert entry.data[CONF_DEFAULT_PASSCODE] == "9753"
    assert manager(hass, entry) is not original_manager
    assert not valve.clients[0].is_connected
    await clock.run(manager(hass, entry).get_connection("valve-a").async_poll())
    assert hass.states.get(usage).state == "975"
    assert hass.states.get(lockout).state == "off"
    assert len(valve.clients) == 2
    assert all(not client.is_connected for client in valve.clients)


async def test_override_takes_precedence_only_for_selected_valve(hass, entry_factory, runtime_factory, bluetooth_io, transport, clock):
    entry = entry_factory(data={CONF_DEFAULT_PASSCODE: "1357"},
                          options={CONF_DEVICE_PASSCODES: {"valve-a": "2468"}})
    await runtime_factory(entry=entry)
    bluetooth_io.advertise("valve-a")
    bluetooth_io.advertise("valve-b")
    await hass.async_block_till_done()
    transport.valve("valve-a").pin = "2468"
    transport.valve("valve-b").pin = "1357"
    transport.valve("valve-a").dashboards.append(dashboard_frames(gallons=246))
    transport.valve("valve-b").dashboards.append(dashboard_frames(gallons=135))
    for address, expected in (("valve-a", "246"), ("valve-b", "135")):
        await clock.run(manager(hass, entry).get_connection(address).async_poll())
        assert hass.states.get(entity_id(hass, "sensor", address, "water_usage_today")).state == expected
        assert hass.states.get(entity_id(hass, "switch", address, "authentication_lockout")).state == "off"
        assert all(not client.is_connected for client in transport.valve(address).clients)


@pytest.mark.parametrize("setting", ["default", "override"])
async def test_correcting_configured_pin_recovers_published_readings(hass, entry_factory, runtime_factory, bluetooth_io, transport, clock, setting):
    data = {CONF_DEFAULT_PASSCODE: "2468" if setting == "default" else "1357"}
    options = {CONF_DEVICE_PASSCODES: {"valve-a": "2468"}} if setting == "override" else {}
    entry = entry_factory(data=data, options=options)
    await runtime_factory(entry=entry)
    bluetooth_io.advertise()
    await hass.async_block_till_done()
    valve = transport.valve()
    valve.pin = "1357"
    valve.dashboards.append(dashboard_frames(gallons=357))
    connection = manager(hass, entry).get_connection("valve-a")
    await clock.run(connection.async_poll())
    usage = entity_id(hass, "sensor", "valve-a", "water_usage_today")
    lockout = entity_id(hass, "switch", "valve-a", "authentication_lockout")
    assert hass.states.get(usage).state == "unknown"
    assert hass.states.get(lockout).state == "on"
    assert connection.last_success is None
    assert valve.dashboard_requests == 0

    # Correcting settings must reach a new manager-owned session through HA's
    # real update listener/reload, rather than a test-supplied PIN getter.
    original_manager = manager(hass, entry)
    correction = ({CONF_DEFAULT_PASSCODE: "1357"} if setting == "default" else
                  {CONF_DEVICE_ADDRESS: "valve-a", CONF_DEVICE_PASSCODE: "1357"})
    await update_options(hass, entry, correction)
    assert manager(hass, entry) is not original_manager
    if setting == "default":
        assert entry.data[CONF_DEFAULT_PASSCODE] == "1357"
    else:
        assert entry.options[CONF_DEVICE_PASSCODES] == {"valve-a": "1357"}
    await clock.run(manager(hass, entry).get_connection("valve-a").async_poll())
    assert hass.states.get(usage).state == "357"
    assert hass.states.get(lockout).state == "off"
    assert len(valve.clients) == 2
    assert all(not client.is_connected for client in valve.clients)
