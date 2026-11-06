"""Real HA objects with temporary storage and simulated external Bluetooth I/O.

The upstream pytest plugin imports homeassistant.runner (and Unix-only fcntl).
On Windows that fails before collection. These fixtures initialize HA's public
objects directly, without replacing their validation or starting host services.
"""

import pytest
from types import MappingProxyType
from homeassistant import loader
from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry, entity_registry, frame

from custom_components.chandler_legacy_view import discovery
from support import BluetoothIO
from ble import Transport
from clock import Clock
from custom_components.chandler_legacy_view import connection


@pytest.fixture
async def hass(tmp_path):
    instance = HomeAssistant(str(tmp_path))
    loader.async_setup(instance)
    frame.async_setup(instance)
    device_registry.async_setup(instance)
    await device_registry.async_load(instance, load_empty=True)
    await entity_registry.async_load(instance, load_empty=True)
    instance.config_entries = ConfigEntries(instance, {})
    await instance.config_entries.async_initialize()
    yield instance
    await instance.async_stop(force=True)


@pytest.fixture
def entry_factory(hass):
    def create(entry_id="test-entry", data=None, options=None):
        entry = ConfigEntry(
            entry_id=entry_id, domain="chandler_legacy_view", title="Test valves",
            data={} if data is None else data, options=options or {},
            source="user", version=1, minor_version=1, unique_id=None,
            subentries_data=None, discovery_keys=MappingProxyType({}),
        )
        # Register without setup, as upstream MockConfigEntry.add_to_hass does.
        # Public async_add also starts integrations and unrelated host services.
        hass.config_entries._entries[entry.entry_id] = entry
        return entry
    return create


@pytest.fixture
def bluetooth_io(monkeypatch):
    io = BluetoothIO()
    monkeypatch.setattr(discovery, "async_register_callback", io.register)
    monkeypatch.setattr(discovery, "async_track_unavailable", io.track_unavailable)
    return io


@pytest.fixture
async def discovery_manager(hass, bluetooth_io):
    manager = discovery.ValveDiscoveryManager(hass, "test-entry")
    await manager.async_setup()
    yield manager
    await manager.async_unload()


@pytest.fixture
def transport(bluetooth_io, monkeypatch):
    simulated = Transport(bluetooth_io)
    monkeypatch.setattr(connection, "establish_connection", simulated.connect)
    monkeypatch.setattr(connection.bluetooth, "async_ble_device_from_address", simulated.resolve)
    return simulated


@pytest.fixture
async def clock(monkeypatch):
    return Clock(monkeypatch)


@pytest.fixture
async def connection_factory(hass, discovery_manager, bluetooth_io, transport):
    connections = []
    def create(address="valve-a", passcode="1357", advertisement=None, state_callback=None):
        bluetooth_io.advertise(address)
        instance = connection.ValveConnection(
            hass, address, "test-entry",
            passcode_getter=lambda _: connection.ValvePasscodeConfiguration(passcode, True),
            connection_state_callback=state_callback or discovery_manager.async_set_connection_state,
        )
        instance.update_from_advertisement(advertisement or discovery_manager.devices[address])
        connections.append(instance)
        return instance
    yield create
    for instance in connections:
        await instance.async_unload()
