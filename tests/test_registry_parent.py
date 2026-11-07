"""Parent links validated by actual config-entry setup and device registration."""

import pytest
from homeassistant.helpers import device_registry as dr, entity_registry as er
from custom_components.chandler_legacy_view.const import DOMAIN, DISCOVERY_VIA_DEVICE_ID


def parent(hass, entry):
    result = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, DISCOVERY_VIA_DEVICE_ID), entry.entry_id)
    assert result is not None
    return result


@pytest.mark.parametrize("cached", [True, False])
async def test_parent_exists_when_valve_entities_are_registered(hass, runtime_factory, bluetooth_io, cached):
    if cached:
        bluetooth_io.advertise()
    entry = await runtime_factory()
    if not cached:
        bluetooth_io.advertise()
    await hass.async_block_till_done()
    registered_parent = parent(hass, entry)
    records = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    assert {record.domain for record in records} == {"binary_sensor", "sensor", "number", "switch"}
    for record in records:
        device = dr.async_get(hass).async_get(record.device_id)
        assert device is not None
        assert device.via_device_id == registered_parent.id
        assert device.config_entry_id == entry.entry_id
        assert (DOMAIN, "valve-a") in device.identifiers


async def test_same_parent_identifier_is_scoped_to_each_config_entry(hass, runtime_factory):
    entries = [await runtime_factory(entry_id) for entry_id in ("first-entry", "second-entry")]
    parents = [parent(hass, entry) for entry in entries]
    assert parents[0].id != parents[1].id
    for entry, device in zip(entries, parents):
        assert device.config_entry_id == entry.entry_id
        assert device.identifiers == {(DOMAIN, DISCOVERY_VIA_DEVICE_ID)}


async def test_parent_link_survives_entry_reload(hass, runtime_factory, bluetooth_io, clock):
    entry = await runtime_factory()
    bluetooth_io.advertise()
    await hass.async_block_till_done()
    registry = dr.async_get(hass)
    child = registry.async_get_device_by_identifier((DOMAIN, "valve-a"), entry.entry_id)
    old_parent = parent(hass, entry)
    assert await clock.run(hass.config_entries.async_reload(entry.entry_id))
    assert parent(hass, entry).id == old_parent.id
    assert registry.async_get(child.id).via_device_id == old_parent.id


async def test_device_metadata_and_parent_link_survive_storage_restore(hass, hass_factory, runtime_factory, bluetooth_io):
    entry = await runtime_factory()
    bluetooth_io.advertise()
    await hass.async_block_till_done()
    registry = dr.async_get(hass)
    child = registry.async_get_device_by_identifier((DOMAIN, "valve-a"), entry.entry_id)
    registered_parent = parent(hass, entry)
    registry.async_update_device(child.id, serial_number="SAVED-SERIAL")
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_stop(force=True)
    restored = await hass_factory(hass.config.config_dir)
    stored = dr.async_get(restored).async_get(child.id)
    assert stored.serial_number == "SAVED-SERIAL"
    assert stored.sw_version == "C4.22"
    assert stored.via_device_id == registered_parent.id
    assert stored.config_entry_id == entry.entry_id
