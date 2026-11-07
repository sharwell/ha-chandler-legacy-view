"""Discovery contracts through real HA objects and simulated scanner callbacks."""

import pytest
from homeassistant.components.bluetooth import BluetoothScanningMode
from custom_components.chandler_legacy_view.const import VALVE_MATCHERS
from custom_components.chandler_legacy_view.discovery import ValveDiscoveryChange as Change


@pytest.fixture
def events(discovery_manager):
    published = []
    discovery_manager.async_add_listener(lambda advertisement, change: published.append((advertisement, change)))
    return published


def changes(events):
    return [change for _, change in events]


async def test_registers_passive_advertisement_callbacks(discovery_manager, bluetooth_io):
    assert [matcher for _, matcher, _ in bluetooth_io.advertisement_callbacks] == list(VALVE_MATCHERS)
    assert all(mode is BluetoothScanningMode.PASSIVE for _, _, mode in bluetooth_io.advertisement_callbacks)
    assert bluetooth_io.unavailable_callbacks == []


async def test_unrelated_advertisement_creates_no_device_or_watcher(discovery_manager, bluetooth_io, events):
    bluetooth_io.advertise(name="Unrelated device")
    assert discovery_manager.devices == {}
    assert events == []
    assert bluetooth_io.unavailable_callbacks == []


async def test_one_connectable_watcher_per_address(discovery_manager, bluetooth_io, events):
    bluetooth_io.advertise()
    bluetooth_io.advertise(rssi=-55)
    bluetooth_io.advertise("valve-b")
    assert [(address, connectable) for _, address, connectable in bluetooth_io.unavailable_callbacks] == [("valve-a", True), ("valve-b", True)]
    bluetooth_io.expire()
    assert set(discovery_manager.devices) == {"valve-b"}
    assert events[-1][0].address == "valve-a"
    assert events[-1][1] is Change.UNAVAILABLE


async def test_loss_publishes_latest_metadata_once(discovery_manager, bluetooth_io, events):
    bluetooth_io.advertise(rssi=-40)
    bluetooth_io.advertise(rssi=-67)
    latest = discovery_manager.devices["valve-a"]
    bluetooth_io.expire()
    bluetooth_io.expire()
    bluetooth_io.expire("unknown")
    assert events[-1][0] is latest
    assert latest.rssi == -67
    assert changes(events) == [Change.AVAILABLE, Change.AVAILABLE, Change.UNAVAILABLE]
    assert discovery_manager.devices == {}


async def test_unrelated_advertisement_cannot_restore_lost_valve(discovery_manager, bluetooth_io, events):
    bluetooth_io.advertise()
    bluetooth_io.expire()
    bluetooth_io.advertise(name="Other device")
    assert discovery_manager.devices == {}
    assert changes(events) == [Change.AVAILABLE, Change.UNAVAILABLE]


async def test_failing_listener_does_not_hide_loss(discovery_manager, bluetooth_io, events, caplog):
    def failing(advertisement, change):
        if change is Change.UNAVAILABLE:
            raise RuntimeError("entity removed during unload")
    discovery_manager.async_add_listener(failing)
    after_failure = []
    discovery_manager.async_add_listener(lambda advertisement, change: after_failure.append(change))
    bluetooth_io.advertise()
    bluetooth_io.expire()
    bluetooth_io.expire()
    assert "entity removed during unload" in caplog.text
    assert after_failure == [Change.AVAILABLE, Change.UNAVAILABLE]
    assert changes(events) == after_failure
    assert discovery_manager.devices == {}


async def test_watcher_survives_recovery_and_second_loss(discovery_manager, bluetooth_io, events):
    bluetooth_io.advertise()
    bluetooth_io.expire()
    bluetooth_io.advertise(rssi=-45)
    assert discovery_manager.devices["valve-a"].rssi == -45
    bluetooth_io.expire()
    assert changes(events) == [Change.AVAILABLE, Change.UNAVAILABLE] * 2
    assert len(bluetooth_io.unavailable_callbacks) == 1
    assert discovery_manager.devices == {}


async def test_partial_recovery_retains_metadata(discovery_manager, bluetooth_io, events):
    bluetooth_io.advertise()
    bluetooth_io.expire()
    bluetooth_io.advertise(rssi=-61, payload=bytes.fromhex("3a07010008150000"))
    recovered = discovery_manager.devices["valve-a"]
    assert (recovered.model, recovered.firmware_version, recovered.valve_type) == ("Evb019", 422, "MeteredSoftener")
    assert recovered.rssi == -61
    assert not recovered.manufacturer_data_complete
    assert not recovered.valve_data_parsed
    assert changes(events) == [Change.AVAILABLE, Change.UNAVAILABLE, Change.AVAILABLE]


async def test_live_connection_defers_scan_loss(discovery_manager, bluetooth_io, events):
    bluetooth_io.advertise()
    discovery_manager.async_set_connection_state("valve-a", True)
    bluetooth_io.expire()
    bluetooth_io.expire()
    assert set(discovery_manager.devices) == {"valve-a"}
    assert changes(events) == [Change.AVAILABLE]
    discovery_manager.async_set_connection_state("valve-a", False)
    discovery_manager.async_set_connection_state("valve-a", False)
    assert changes(events) == [Change.AVAILABLE, Change.UNAVAILABLE]
    assert discovery_manager.devices == {}


async def test_connection_after_scan_loss_restores_presence(discovery_manager, bluetooth_io, events):
    bluetooth_io.advertise()
    bluetooth_io.expire()
    discovery_manager.async_set_connection_state("valve-a", True)
    discovery_manager.async_set_connection_state("valve-a", True)
    assert set(discovery_manager.devices) == {"valve-a"}
    discovery_manager.async_set_connection_state("valve-a", False)
    assert changes(events) == [Change.AVAILABLE, Change.UNAVAILABLE] * 2


async def test_fresh_advertisement_keeps_presence_after_disconnect(discovery_manager, bluetooth_io, events):
    bluetooth_io.advertise()
    discovery_manager.async_set_connection_state("valve-a", True)
    bluetooth_io.expire()
    bluetooth_io.advertise(rssi=-42)
    discovery_manager.async_set_connection_state("valve-a", False)
    assert discovery_manager.devices["valve-a"].rssi == -42
    assert changes(events) == [Change.AVAILABLE, Change.AVAILABLE]


async def test_connection_cannot_invent_unknown_device(discovery_manager, bluetooth_io, events):
    discovery_manager.async_set_connection_state("unknown", True)
    discovery_manager.async_set_connection_state("unknown", False)
    assert discovery_manager.devices == {}
    assert events == []
    assert bluetooth_io.unavailable_callbacks == []


async def test_unload_releases_all_subscriptions(discovery_manager, bluetooth_io):
    bluetooth_io.advertise()
    bluetooth_io.advertise("valve-b")
    bluetooth_io.expire()
    discovery_manager.async_set_connection_state("valve-b", True)
    await discovery_manager.async_unload()
    await discovery_manager.async_unload()
    assert bluetooth_io.advertisement_callbacks == []
    assert bluetooth_io.unavailable_callbacks == []
    assert discovery_manager.devices == {}


async def test_queued_callbacks_cannot_revive_unloaded_manager(discovery_manager, bluetooth_io, events):
    info = bluetooth_io.advertise()
    late_advertisement = bluetooth_io.queued_advertisement(info)
    late_loss = bluetooth_io.queued_loss()
    await discovery_manager.async_unload()
    late_advertisement()
    late_loss()
    discovery_manager.async_set_connection_state("valve-a", True)
    assert discovery_manager.devices == {}
    assert changes(events) == [Change.AVAILABLE]
    assert bluetooth_io.advertisement_callbacks == []
    assert bluetooth_io.unavailable_callbacks == []
