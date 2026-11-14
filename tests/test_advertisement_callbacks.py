"""Packet-only changes reach real HA entities without changing discovery rules."""

import pytest
from bluetooth_data_tools import monotonic_time_coarse
from homeassistant.components import bluetooth
from homeassistant.helpers import entity_registry as er

from bluetooth_replay import BluetoothReplay
from custom_components.chandler_legacy_view.const import DATA_DISCOVERY_MANAGER, DOMAIN
from packets import MODERN_422, raw_advertisement


@pytest.fixture
def bluetooth_io(hass, monkeypatch):
    replay = BluetoothReplay(hass, monkeypatch)
    yield replay
    replay.close()


@pytest.fixture
def packet_api(monkeypatch):
    if not hasattr(bluetooth, "async_register_advertisement_callback"):
        pytest.skip("This HA version has only change-driven advertisement callbacks")


def combined_report(*, status=1, minute=41, firmware_minor=0x40):
    """Build fictional C4.40 data split into two same-company fields."""
    prefix = bytes((0x3A, 7, status, 0, 9, minute, 128, 99))
    metadata = bytes((0x3A, 7, 1, 2, 3, 1, 4, firmware_minor))
    return raw_advertisement(prefix, metadata) + b"\x0e\x09CS_Meter_Soft"


async def setup_entities(hass, entry_factory, runtime_factory):
    """Load one entry with automatic polling off; return its discovery manager."""
    entry = entry_factory()
    hass.config_entries.async_update_entry(entry, pref_disable_polling=True)
    await runtime_factory(entry=entry)
    return hass.data[DOMAIN][entry.entry_id][DATA_DISCOVERY_MANAGER]


def state(hass, suffix=""):
    """Return valve-a's registered presence, salt, or bypass entity state."""
    identifier = er.async_get(hass).async_get_entity_id("binary_sensor", DOMAIN, f"valve-a{suffix}")
    assert identifier is not None
    return hass.states.get(identifier)


async def test_packet_only_status_and_clock_changes_update_entities(
    hass, entry_factory, runtime_factory, bluetooth_io, packet_api,
):
    await setup_entities(hass, entry_factory, runtime_factory)
    bluetooth_io.advertise(raw=combined_report())
    await hass.async_block_till_done()
    assert state(hass, "_salt").state == "off"
    assert state(hass, "_bypass").state == "off"
    # The generic manufacturer dictionary is unchanged. These changes must
    # still reach HA, or low-salt and bypass remain frozen at their old values.
    bluetooth_io.advertise(raw=combined_report(status=0x0F, minute=42))
    await hass.async_block_till_done()
    assert state(hass, "_salt").state == "on"
    assert state(hass, "_bypass").state == "on"
    assert state(hass).attributes["water_status"] == "Water off"
    assert state(hass).attributes["valve_time_minutes"] == 42
    assert state(hass).attributes["firmware_version"] == "C4.40"
    bluetooth_io.advertise(raw=combined_report(minute=43))
    await hass.async_block_till_done()
    assert state(hass, "_salt").state == "off"
    assert state(hass, "_bypass").state == "off"
    assert state(hass).attributes["valve_time_minutes"] == 43


async def test_missing_packet_api_preserves_discovery_replay_and_changed_updates(
    hass, entry_factory, runtime_factory, bluetooth_io, monkeypatch,
):
    monkeypatch.delattr(bluetooth, "async_register_advertisement_callback", raising=False)
    bluetooth_io.advertise(raw=combined_report())
    await setup_entities(hass, entry_factory, runtime_factory)
    await hass.async_block_till_done()
    assert state(hass).state == "on"
    assert state(hass).attributes["firmware_version"] == "C4.40"
    bluetooth_io.advertise(raw=combined_report(status=0x0B, minute=42, firmware_minor=0x41))
    await hass.async_block_till_done()
    assert state(hass, "_salt").state == "on"
    assert state(hass, "_bypass").state == "on"
    assert state(hass).attributes["firmware_version"] == "C4.41"


async def test_existing_discovery_history_also_acquires_live_packet_updates(
    hass, entry_factory, runtime_factory, bluetooth_io, packet_api,
):
    bluetooth_io.advertise(raw=combined_report())
    await setup_entities(hass, entry_factory, runtime_factory)
    await hass.async_block_till_done()
    assert state(hass, "_salt").state == "off"
    bluetooth_io.advertise(raw=combined_report(status=0x0B, minute=42))
    await hass.async_block_till_done()
    assert state(hass, "_salt").state == "on"
    assert state(hass).attributes["valve_time_minutes"] == 42


async def test_discovery_and_packet_callbacks_do_not_duplicate_or_reorder_updates(
    discovery_manager, bluetooth_io, hass, packet_api,
):
    published = []
    discovery_manager.async_add_listener(lambda ad, change: published.append((ad.salt_sensor_status, ad.valve_time_minutes)))
    bluetooth_io.advertise(raw=combined_report())
    await hass.async_block_till_done()
    # A queued raw-only change is superseded by a later normal callback.
    bluetooth_io.advertise(raw=combined_report(status=0x0B, minute=42))
    bluetooth_io.advertise(raw=combined_report(minute=43, firmware_minor=0x41))
    await hass.async_block_till_done()
    bluetooth_io.advertise(raw=combined_report(minute=43, firmware_minor=0x41))
    await hass.async_block_till_done()
    assert published == [(0, 41), (0, 43)]


async def test_multiple_raw_only_changes_keep_their_published_order(
    discovery_manager, bluetooth_io, hass, packet_api,
):
    bluetooth_io.advertise(raw=combined_report())
    published = []
    discovery_manager.async_add_listener(lambda ad, change: published.append(ad.salt_sensor_status))
    bluetooth_io.advertise(raw=combined_report(status=0x0B, minute=42))
    bluetooth_io.advertise(raw=combined_report(minute=43))
    await hass.async_block_till_done()
    assert published == [1, 0]


async def test_rejected_weaker_adapter_cannot_replace_current_status(
    discovery_manager, bluetooth_io, hass, packet_api,
):
    bluetooth_io.add_scanner("scanner-b")
    bluetooth_io.advertise(raw=combined_report(), rssi=-40)
    bluetooth_io.advertise(raw=combined_report(status=0x0B, minute=42), source="scanner-b", rssi=-85)
    await hass.async_block_till_done()
    valve = discovery_manager.devices["valve-a"]
    assert (valve.salt_sensor_status, valve.bypass_status, valve.valve_time_minutes) == (0, 0, 41)
    bluetooth_io.advertise(raw=combined_report(status=0x0B, minute=43), rssi=-40)
    await hass.async_block_till_done()
    assert discovery_manager.devices["valve-a"].salt_sensor_status == 1


async def test_selected_adapter_handover_uses_cached_name_and_fresh_raw_status(
    discovery_manager, bluetooth_io, hass, packet_api,
):
    bluetooth_io.add_scanner("scanner-b")
    bluetooth_io.advertise(raw=combined_report(), rssi=-80)
    # This scanner has never heard the local name. HA supplies its cross-source
    # cached name after the raw callback, before our deferred packet handling.
    no_name = combined_report(status=0x0B, minute=42)[:-15]
    bluetooth_io.advertise(raw=no_name, source="scanner-b", rssi=-30)
    await hass.async_block_till_done()
    valve = discovery_manager.devices["valve-a"]
    assert (valve.name, valve.salt_sensor_status, valve.valve_time_minutes) == ("CS_Meter_Soft", 1, 42)
    bluetooth_io.advertise(raw=combined_report(minute=43), rssi=-80)
    await hass.async_block_till_done()
    assert discovery_manager.devices["valve-a"].salt_sensor_status == 1


async def test_another_connectable_adapter_keeps_the_valve_available(
    discovery_manager, bluetooth_io, hass, packet_api,
):
    bluetooth_io.add_scanner("scanner-b")
    bluetooth_io.advertise(raw=combined_report(), observed_at=monotonic_time_coarse() - 10000)
    bluetooth_io.advertise(raw=combined_report(status=0x0B, minute=42), source="scanner-b")
    await hass.async_block_till_done()
    bluetooth_io.expire()
    assert discovery_manager.devices["valve-a"].salt_sensor_status == 1


async def test_nonconnectable_reports_cannot_revive_an_unavailable_valve(
    discovery_manager, bluetooth_io, hass, packet_api,
):
    bluetooth_io.add_scanner("passive-only", connectable=False)
    bluetooth_io.advertise(raw=combined_report(), observed_at=monotonic_time_coarse() - 1000)
    bluetooth_io.expire()
    assert discovery_manager.devices == {}
    bluetooth_io.advertise(raw=combined_report(status=0x0B, minute=42), source="passive-only")
    await hass.async_block_till_done()
    assert discovery_manager.devices == {}
    bluetooth_io.advertise(raw=combined_report(minute=43))
    await hass.async_block_till_done()
    assert discovery_manager.devices["valve-a"].valve_time_minutes == 43


async def test_queued_raw_update_cannot_undo_home_assistant_expiry(
    discovery_manager, bluetooth_io, hass, packet_api,
):
    bluetooth_io.advertise(raw=combined_report())
    bluetooth_io.advertise(raw=combined_report(status=0x0B, minute=42),
                           observed_at=monotonic_time_coarse() - 1000)
    bluetooth_io.expire()
    await hass.async_block_till_done()
    assert discovery_manager.devices == {}


async def test_identical_raw_recovery_keeps_availability_after_session_ends(
    discovery_manager, bluetooth_io, hass, packet_api,
):
    bluetooth_io.add_scanner("passive-only", connectable=False)
    bluetooth_io.advertise(raw=combined_report(), rssi=-80,
                           observed_at=monotonic_time_coarse() - 1000)
    discovery_manager.async_set_connection_state("valve-a", True)
    bluetooth_io.expire()
    assert "valve-a" in discovery_manager.devices
    # A passive source keeps the same merged data in HA's overall history, so
    # the returning connectable source reaches only the packet callback.
    bluetooth_io.advertise(raw=combined_report(), source="passive-only", rssi=-30)
    bluetooth_io.advertise(raw=combined_report(), rssi=-80)
    await hass.async_block_till_done()
    discovery_manager.async_set_connection_state("valve-a", False)
    assert "valve-a" in discovery_manager.devices


async def test_multiple_valves_receive_independent_packet_updates(
    discovery_manager, bluetooth_io, hass, packet_api,
):
    bluetooth_io.advertise(raw=combined_report())
    bluetooth_io.advertise("valve-b", raw=combined_report())
    bluetooth_io.advertise(raw=combined_report(status=0x0B, minute=42))
    bluetooth_io.advertise("valve-b", raw=combined_report(minute=43))
    await hass.async_block_till_done()
    assert discovery_manager.devices["valve-a"].salt_sensor_status == 1
    assert discovery_manager.devices["valve-b"].salt_sensor_status == 0
    assert discovery_manager.devices["valve-b"].valve_time_minutes == 43


async def test_rawless_backend_continues_to_use_normal_changed_updates(
    discovery_manager, bluetooth_io, hass, packet_api,
):
    bluetooth_io.advertise(payload=MODERN_422, raw_available=False)
    changed = bytearray(MODERN_422)
    changed[2] = 0x0B
    bluetooth_io.advertise(payload=bytes(changed), raw_available=False)
    await hass.async_block_till_done()
    assert discovery_manager.devices["valve-a"].salt_sensor_status == 1


async def test_pending_packets_and_subscriptions_are_removed_on_unload(
    discovery_manager, bluetooth_io, hass, packet_api,
):
    bluetooth_io.advertise(raw=combined_report())
    published = []
    discovery_manager.async_add_listener(lambda ad, change: published.append(change))
    bluetooth_io.advertise(raw=combined_report(status=0x0B, minute=42))
    await discovery_manager.async_unload()
    bluetooth_io.advertise(raw=combined_report(status=0x0B, minute=43))
    await hass.async_block_till_done()
    assert published == []
    assert discovery_manager.devices == {}
