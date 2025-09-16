"""The Chandler Legacy View integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.typing import ConfigType

from .const import (
    DATA_DISCOVERY_MANAGER,
    DEFAULT_MANUFACTURER,
    DISCOVERY_DEVICE_MODEL,
    DISCOVERY_DEVICE_NAME,
    DISCOVERY_VIA_DEVICE_ID,
    DOMAIN,
    PLATFORMS,
)
from .discovery import ValveDiscoveryManager

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Chandler Legacy View integration via YAML."""

    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Chandler Legacy View from a config entry."""

    hass.data.setdefault(DOMAIN, {})

    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, DISCOVERY_VIA_DEVICE_ID)},
        manufacturer=DEFAULT_MANUFACTURER,
        model=DISCOVERY_DEVICE_MODEL,
        name=DISCOVERY_DEVICE_NAME,
        entry_type=DeviceEntryType.SERVICE,
    )

    manager = ValveDiscoveryManager(hass)
    await manager.async_setup()

    hass.data[DOMAIN][entry.entry_id] = {DATA_DISCOVERY_MANAGER: manager}

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    _LOGGER.debug("Chandler Legacy View setup complete for entry %s", entry.entry_id)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Chandler Legacy View config entry."""

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    data = hass.data[DOMAIN].pop(entry.entry_id, None)
    if data is not None:
        await data[DATA_DISCOVERY_MANAGER].async_unload()

    if not hass.data[DOMAIN]:
        hass.data.pop(DOMAIN)

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle a config entry reload request."""

    await hass.config_entries.async_reload(entry.entry_id)
