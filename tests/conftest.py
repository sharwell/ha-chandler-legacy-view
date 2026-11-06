"""Real HA objects with temporary storage and simulated external Bluetooth I/O.

The upstream pytest plugin imports homeassistant.runner (and Unix-only fcntl).
On Windows that fails before collection. These fixtures initialize HA's public
objects directly, without replacing their validation or starting host services.
"""

import pytest
from types import MappingProxyType
from pathlib import Path
import socket
from homeassistant import loader
from homeassistant.config_entries import ConfigEntries, ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry, category_registry, device_registry, entity_registry,
    floor_registry, frame, label_registry, issue_registry, entity,
    restore_state, translation,
)
from custom_components import chandler_legacy_view as integration
from custom_components.chandler_legacy_view.const import DOMAIN

from custom_components.chandler_legacy_view import discovery
from support import BluetoothIO
from ble import Transport
from clock import Clock
from custom_components.chandler_legacy_view import connection


@pytest.fixture
async def hass_factory(tmp_path, monkeypatch):
    def deny_host_io(*args, **kwargs):
        raise PermissionError("Host socket I/O is disabled in the HA test runtime")
    # The asyncio runner's wakeup sockets already exist before this async
    # fixture starts. Real HA objects may not connect to local services, bind
    # server ports, or send datagrams while their fixtures are active.
    for operation in ("connect", "connect_ex", "bind", "listen", "sendto"):
        monkeypatch.setattr(socket.socket, operation, deny_host_io)
    instances = []
    async def create(config_dir):
        if not Path(config_dir).resolve().is_relative_to(tmp_path.resolve()):
            raise ValueError("HA test storage must stay inside this test's directory")
        instance = HomeAssistant(str(config_dir))
        instances.append(instance)
        instance.config.skip_pip = True
        loader.async_setup(instance)
        frame.async_setup(instance)
        entity.async_setup(instance)
        translation.async_setup(instance)
        device_registry.async_setup(instance)
        await device_registry.async_load(instance)
        await entity_registry.async_load(instance)
        for registry in (area_registry, category_registry, floor_registry,
                         label_registry, issue_registry):
            await registry.async_load(instance)
        await restore_state.async_load(instance)
        instance.config_entries = ConfigEntries(instance, {})
        await instance.config_entries.async_initialize()
        return instance
    yield create
    for instance in reversed(instances):
        await instance.async_stop(force=True)


@pytest.fixture
async def hass(tmp_path, hass_factory):
    return await hass_factory(tmp_path)

@pytest.fixture
async def runtime_factory(hass, entry_factory, bluetooth_io, transport):
    entries = []
    async def create(entry_id="test-entry", entry=None):
        entry = entry or entry_factory(entry_id)
        # Initialize this component without bootstrapping HA's Bluetooth/USB
        # host services. Scanner and GATT boundaries are supplied by fixtures.
        await integration.async_setup(hass, {})
        hass.config.components.add(DOMAIN)
        await entry.async_setup_locked(hass)
        assert entry.state is ConfigEntryState.LOADED, entry.reason
        entries.append(entry)
        return entry
    yield create
    for entry in reversed(entries):
        if entry.state is ConfigEntryState.LOADED:
            await hass.config_entries.async_unload(entry.entry_id)


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
async def clock(monkeypatch, request):
    return Clock(monkeypatch, start=getattr(request, "param", None))


@pytest.fixture
async def connection_factory(hass, discovery_manager, bluetooth_io, transport):
    connections = []
    def create(address="valve-a", passcode="1234", advertisement=None, state_callback=None,
               entry_id="test-entry"):
        if address not in bluetooth_io.history:
            # A standalone connection may receive metadata at its module
            # boundary. Do not fabricate a complete scanner observation when
            # the scenario supplies partial metadata or already has an advert.
            observation = {} if advertisement is None else {
                "name": advertisement.name, "rssi": advertisement.rssi,
                "manufacturer_data": advertisement.manufacturer_data,
            }
            bluetooth_io.advertise(address, **observation)
        instance = connection.ValveConnection(
            hass, address, entry_id,
            passcode_getter=lambda _: connection.ValvePasscodeConfiguration(passcode, True),
            connection_state_callback=state_callback or discovery_manager.async_set_connection_state,
        )
        instance.update_from_advertisement(advertisement or discovery_manager.devices[address])
        connections.append(instance)
        return instance
    yield create
    for instance in connections:
        await instance.async_unload()
