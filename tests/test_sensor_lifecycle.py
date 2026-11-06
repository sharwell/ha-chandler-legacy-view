"""Sensor defaults, lifecycle, and retained choices using HA's own registries."""

from datetime import datetime
import logging
import pytest
from homeassistant.helpers import entity_registry as er
from custom_components.chandler_legacy_view.const import DOMAIN, DATA_CONNECTION_MANAGER
from packets import dashboard_frames


def sensor_id(hass, suffix):
    result = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"valve-a_{suffix}")
    assert result is not None
    return result


def connection(hass, entry):
    return hass.data[DOMAIN][entry.entry_id][DATA_CONNECTION_MANAGER].get_connection("valve-a")


async def test_clock_default_is_disabled_but_shared_data_stays_fresh(hass, runtime_factory, bluetooth_io, transport, clock):
    entry = await runtime_factory()
    bluetooth_io.advertise()
    await hass.async_block_till_done()
    identifier = sensor_id(hass, "time_of_day")
    assert er.async_get(hass).async_get(identifier).disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert hass.states.get(identifier) is None
    current = connection(hass, entry)
    transport.valve().dashboards.append(dashboard_frames(minute=35, gallons=135))
    await clock.run(current.async_poll())
    assert current.dashboard_data.time_minute == 35
    assert hass.states.get(identifier) is None
    assert hass.states.get(sensor_id(hass, "water_usage_today")).state == "135"


async def test_enabling_clock_uses_real_lifecycle_and_current_reading(hass, runtime_factory, bluetooth_io, transport, clock):
    entry = await runtime_factory()
    bluetooth_io.advertise()
    await hass.async_block_till_done()
    identifier = sensor_id(hass, "time_of_day")
    er.async_get(hass).async_update_entity(identifier, disabled_by=None)
    await clock.until(lambda: hass.states.get(identifier) is not None)
    transport.valve().dashboards.append(dashboard_frames(hour=4, minute=36))
    await clock.run(connection(hass, entry).async_poll())
    published = datetime.fromisoformat(hass.states.get(identifier).state)
    assert (published.hour, published.minute) == (4, 36)
    assert er.async_get(hass).async_get(identifier).disabled_by is None


@pytest.mark.parametrize("suffix", ["time_of_day", "water_usage_today"])
async def test_disabled_sensor_stops_publication_while_polling_continues(hass, entry_factory, runtime_factory, bluetooth_io, transport, clock, caplog, suffix):
    entry = entry_factory()
    registry = er.async_get(hass)
    registry.async_get_or_create("sensor", DOMAIN, f"valve-a_{suffix}", config_entry=entry, disabled_by=None)
    await runtime_factory(entry=entry)
    bluetooth_io.advertise()
    await hass.async_block_till_done()
    identifier = sensor_id(hass, suffix)
    current = connection(hass, entry)
    await clock.run(current.async_poll())
    assert hass.states.get(identifier) is not None
    registry.async_update_entity(identifier, disabled_by=er.RegistryEntryDisabler.USER)
    await hass.async_block_till_done()
    assert hass.states.get(identifier) is None
    await clock.advance(31)
    caplog.clear()
    transport.valve().dashboards.append(dashboard_frames(minute=37, gallons=237))
    await clock.run(current.async_poll())
    assert current.dashboard_data.water_usage == 237
    assert current.dashboard_data.time_minute == 37
    assert hass.states.get(identifier) is None
    # A lingering subscription would attempt to publish through the removed
    # HA entity and report a write/lifecycle error.
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]


@pytest.mark.parametrize("disabled_by", [None, er.RegistryEntryDisabler.USER])
async def test_existing_clock_choice_survives_real_entry_reload(hass, entry_factory, runtime_factory, bluetooth_io, clock, disabled_by):
    entry = entry_factory()
    registry = er.async_get(hass)
    existing = registry.async_get_or_create("sensor", DOMAIN, "valve-a_time_of_day", config_entry=entry, disabled_by=disabled_by)
    await runtime_factory(entry=entry)
    bluetooth_io.advertise()
    await hass.async_block_till_done()
    assert registry.async_get(existing.entity_id).disabled_by is disabled_by
    assert await clock.run(hass.config_entries.async_reload(entry.entry_id))
    assert registry.async_get(existing.entity_id).disabled_by is disabled_by
    assert (hass.states.get(existing.entity_id) is None) == (disabled_by is not None)


async def test_clock_choice_is_restored_from_actual_registry_storage(hass, hass_factory, entry_factory, runtime_factory, bluetooth_io):
    entry = entry_factory()
    registry = er.async_get(hass)
    existing = registry.async_get_or_create("sensor", DOMAIN, "valve-a_time_of_day", config_entry=entry, disabled_by=None)
    await runtime_factory(entry=entry)
    bluetooth_io.advertise()
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_stop(force=True)
    restored = await hass_factory(hass.config.config_dir)
    saved = er.async_get(restored).async_get(existing.entity_id)
    assert saved is not None
    assert saved.disabled_by is None
    assert saved.unique_id == "valve-a_time_of_day"
