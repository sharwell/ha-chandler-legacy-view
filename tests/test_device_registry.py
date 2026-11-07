"""Owned metadata changes against Home Assistant's real device registry."""

import pytest
from homeassistant.helpers import device_registry as dr
from custom_components.chandler_legacy_view import device_registry as helpers
from custom_components.chandler_legacy_view.const import DOMAIN
from packets import device_list_frame

UPDATES = [
    (helpers.async_update_device_model, "model", "Evb034"),
    (helpers.async_update_device_serial_number, "serial_number", "SERIAL-NEW"),
    (helpers.async_update_device_sw_version, "sw_version", "C4.22"),
]


@pytest.fixture
def devices(hass, entry_factory):
    registry = dr.async_get(hass)
    def create(entry_id, value="old"):
        entry = entry_factory(entry_id)
        return registry.async_get_or_create(config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, "valve-a")}, serial_number=value, sw_version=value, model=value)
    return registry, create


@pytest.mark.parametrize("update,field,value", UPDATES)
@pytest.mark.parametrize("owner", ["entry-a", "entry-b"])
async def test_metadata_updates_only_requested_owner(hass, devices, update, field, value, owner):
    registry, create = devices
    records = {entry_id: create(entry_id) for entry_id in ("entry-a", "entry-b")}
    update(hass, owner, "valve-a", value)
    assert {entry_id: getattr(registry.async_get(record.id), field) for entry_id, record in records.items()} == {
        entry_id: value if entry_id == owner else "old" for entry_id in records}


@pytest.mark.parametrize("update,field,value", UPDATES)
@pytest.mark.parametrize("already_empty", [False, True])
async def test_unchanged_metadata_publishes_no_registry_update(hass, devices, update, field, value, already_empty):
    registry, create = devices
    value = None if already_empty else value
    device = create("entry-a", value)
    await hass.async_block_till_done()
    events = []
    remove = hass.bus.async_listen(dr.EVENT_DEVICE_REGISTRY_UPDATED, events.append)
    try:
        update(hass, "entry-a", "valve-a", value)
        await hass.async_block_till_done()
        assert events == []
        assert getattr(registry.async_get(device.id), field) == value
    finally:
        remove()


@pytest.mark.parametrize("update,field,value", UPDATES)
async def test_missing_owner_does_not_update_other_entry(hass, devices, update, field, value):
    registry, create = devices
    other = create("entry-b", "other")
    update(hass, "missing-entry", "valve-a", value)
    assert getattr(registry.async_get(other.id), field) == "other"


@pytest.mark.parametrize("update,field,value", UPDATES)
async def test_none_clears_owned_metadata(hass, devices, update, field, value):
    registry, create = devices
    own = create("entry-a")
    other = create("entry-b")
    update(hass, "entry-a", "valve-a", None)
    assert getattr(registry.async_get(own.id), field) is None
    assert getattr(registry.async_get(other.id), field) == "old"


async def test_device_list_updates_and_clears_only_owning_record(hass, devices, connection_factory, transport, clock):
    registry, create = devices
    own = create("entry-a")
    other = create("entry-b", "other")
    connection = connection_factory(entry_id="entry-a")
    valve = transport.valve()
    for serial, expected in [(bytes.fromhex("12345678"), "12345678"), (bytes.fromhex("FFFFFFFF"), None)]:
        valve.device_lists.append([device_list_frame(status=128, serial=serial)])
        await clock.run(connection.async_poll())
        assert connection.serial_number == expected
        assert registry.async_get(own.id).serial_number == expected
        assert registry.async_get(other.id).serial_number == "other"
        await clock.advance(31)
    assert len(valve.clients) == 2
    assert all(not client.is_connected for client in valve.clients)
