"""Sensor defaults, lifecycle, and retained choices using HA's own registries."""

from datetime import UTC, datetime
import logging
import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
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


@pytest.mark.parametrize("is_pm,expected_hour", [(False, 4), (True, 16)])
async def test_enabling_clock_uses_real_lifecycle_and_current_reading(hass, runtime_factory, bluetooth_io, transport, clock, is_pm, expected_hour):
    entry = await runtime_factory()
    bluetooth_io.advertise()
    await hass.async_block_till_done()
    identifier = sensor_id(hass, "time_of_day")
    er.async_get(hass).async_update_entity(identifier, disabled_by=None)
    await clock.until(lambda: hass.states.get(identifier) is not None)
    transport.valve().dashboards.append(dashboard_frames(hour=4, minute=36, is_pm=is_pm))
    await clock.run(connection(hass, entry).async_poll())
    published = datetime.fromisoformat(hass.states.get(identifier).state)
    expected = dt_util.as_local(dt_util.utcnow()).replace(
        hour=expected_hour, minute=36, second=0, microsecond=0)
    assert published.isoformat() == expected.astimezone(UTC).isoformat()
    assert er.async_get(hass).async_get(identifier).disabled_by is None


@pytest.mark.parametrize("time_zone,clock,before,after", [
    ("UTC", datetime(2026, 1, 31, 23, 59, tzinfo=UTC),
     "2026-01-31T23:59:00+00:00", "2026-02-01T00:01:00+00:00"),
    ("America/Chicago", datetime(2026, 2, 1, 5, 59, tzinfo=UTC),
     "2026-01-31T23:59:00-06:00", "2026-02-01T00:01:00-06:00"),
], indirect=["clock"], ids=["UTC-midnight", "Chicago-midnight"])
async def test_clock_publishes_complete_timestamps_across_local_midnight(hass, entry_factory, runtime_factory, bluetooth_io, transport, clock, time_zone, before, after):
    original_time_zone = hass.config.time_zone
    try:
        await hass.config.async_set_time_zone(time_zone)
        entry = entry_factory()
        record = er.async_get(hass).async_get_or_create("sensor", DOMAIN, "valve-a_time_of_day",
                                                       config_entry=entry, disabled_by=None)
        await runtime_factory(entry=entry)
        bluetooth_io.advertise()
        await hass.async_block_till_done()
        valve = transport.valve()
        for index, (expected_local, hour, minute, is_pm) in enumerate(
            ((before, 11, 59, True), (after, 12, 1, False))
        ):
            if index:
                # Advance the real retry deadline as well as the calendar.
                # A stale local clock would date the fresh reading yesterday.
                await clock.advance(120)
            valve.dashboards.append(dashboard_frames(hour=hour, minute=minute, is_pm=is_pm))
            await clock.run(connection(hass, entry).async_poll())
            published = hass.states.get(record.entity_id).state
            expected = datetime.fromisoformat(expected_local)
            # HA serializes timestamp sensors in UTC; verify both that public
            # state and the corresponding configured local date and offset.
            assert published == expected.astimezone(UTC).isoformat()
            local = dt_util.as_local(datetime.fromisoformat(published))
            assert local.isoformat() == expected_local
    finally:
        await hass.config.async_set_time_zone(original_time_zone)


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


async def test_dashboard_subscriber_receives_current_cached_reading(connection_factory, transport, clock):
    current = connection_factory()
    transport.valve().dashboards.append(dashboard_frames(minute=35, gallons=135))
    await clock.run(current.async_poll())
    received = []
    remove = current.add_dashboard_listener(received.append)
    await clock.drain()
    assert len(received) == 1
    assert (received[0].time_minute, received[0].water_usage) == (35, 135)
    remove()
    await clock.advance(31)
    await clock.run(current.async_poll())
    assert len(received) == 1
