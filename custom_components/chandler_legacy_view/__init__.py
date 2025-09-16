"""The Chandler Legacy View integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

from .const import DATA_DISCOVERY_MANAGER, DOMAIN, PLATFORMS
from .discovery import ValveDiscoveryManager

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Chandler Legacy View integration via YAML."""

    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Chandler Legacy View from a config entry."""

    hass.data.setdefault(DOMAIN, {})

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
