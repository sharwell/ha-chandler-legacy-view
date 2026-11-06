"""Real HA objects with temporary storage and simulated external Bluetooth I/O.

The upstream pytest plugin imports homeassistant.runner (and Unix-only fcntl).
On Windows that fails before collection. These fixtures initialize HA's public
objects directly, without replacing their validation or starting host services.
"""

import pytest
from homeassistant import loader
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry, entity_registry, frame

from custom_components.chandler_legacy_view import discovery
from support import BluetoothIO


@pytest.fixture
async def hass(tmp_path):
    instance = HomeAssistant(str(tmp_path))
    loader.async_setup(instance)
    frame.async_setup(instance)
    device_registry.async_setup(instance)
    await device_registry.async_load(instance, load_empty=True)
    await entity_registry.async_load(instance, load_empty=True)
    yield instance
    await instance.async_stop(force=True)


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
