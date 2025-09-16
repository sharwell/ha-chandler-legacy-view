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

# Known Bluetooth local-name prefixes advertised by Chandler Legacy valves.
VALVE_NAME_PREFIXES: Final[tuple[str, ...]] = ("CS_", "C2_", "CL_")

# Bluetooth callback matchers describing the devices we are interested in.
#
# Chandler's firmware appears to emit identifiers starting with ``CS_``,
# ``C2_``, or ``CL_`` (case-insensitive). ``BluetoothCallbackMatcher``
# local-name matching uses ``fnmatch`` semantics, so we express the
# case-insensitive prefixes via character classes.
VALVE_MATCHERS: Final[tuple[dict[str, str], ...]] = (
    {"local_name": "[Cc][Ss]_*"},
    {"local_name": "[Cc]2_*"},
    {"local_name": "[Cc][Ll]_*"},
)
