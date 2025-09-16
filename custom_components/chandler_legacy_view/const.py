"""Constants for the Chandler Legacy View integration."""

from __future__ import annotations

from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "chandler_legacy_view"
PLATFORMS: Final[list[Platform]] = [Platform.BINARY_SENSOR]

# Storage keys used inside ``hass.data``
DATA_DISCOVERY_MANAGER: Final = "discovery_manager"

# Default presentation details for discovered devices
DEFAULT_DEVICE_NAME: Final = "Water System Valve"
DEFAULT_MANUFACTURER: Final = "Chandler"

# Bluetooth callback matchers describing the devices we are interested in.
# These are placeholders that document the expected shape of the data and can be
# updated once precise manufacturer or service identifiers are known.
VALVE_MATCHERS: Final = (
    {"local_name": "Chandler Softener"},
    {"local_name": "Chandler Filter"},
)
