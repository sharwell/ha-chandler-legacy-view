"""Constants for the Chandler Legacy View integration."""

from __future__ import annotations

from datetime import timedelta
from itertools import product
from typing import Final

from homeassistant.const import Platform

CONF_DEFAULT_PASSCODE: Final = "default_passcode"
CONF_DEVICE_ADDRESS: Final = "device_address"
CONF_DEVICE_PASSCODE: Final = "device_passcode"
CONF_DEVICE_PASSCODES: Final = "device_passcodes"
CONF_REMOVE_OVERRIDE: Final = "remove_override"

DOMAIN: Final = "chandler_legacy_view"
PLATFORMS: Final[list[Platform]] = [Platform.BINARY_SENSOR, Platform.SENSOR]

# Storage keys used inside ``hass.data``
DATA_DISCOVERY_MANAGER: Final = "discovery_manager"
DATA_CONNECTION_MANAGER: Final = "connection_manager"

# Polling configuration for on-demand Bluetooth connections
CONNECTION_POLL_INTERVAL: Final = timedelta(minutes=15)
CONNECTION_MIN_RETRY_INTERVAL: Final = timedelta(seconds=15)
CONNECTION_TIMEOUT_SECONDS: Final = 20

# Default presentation details for discovered devices
DEFAULT_FRIENDLY_NAME: Final = "Treatment Valve"
DEFAULT_MANUFACTURER: Final = "Chandler"

# Device registry definitions for the integration's Bluetooth discovery service.
DISCOVERY_VIA_DEVICE_ID: Final = "bluetooth"
DISCOVERY_DEVICE_NAME: Final = "Chandler Valve Discovery"
DISCOVERY_DEVICE_MODEL: Final = "Bluetooth Service"

# Manufacturer data identifier advertised by Chandler Legacy valves.
CSI_MANUFACTURER_ID: Final = 1850

# Mapping of known Bluetooth local names to user-friendly descriptions.
FRIENDLY_NAME_OVERRIDES: Final[dict[str, str]] = {
    "c2_1a": "Backwashing Filter",
    "c2_ff": "Backwashing Filter",
    "c2_1b": "Backwashing Filter",
    "c2_04": "Backwashing Filter",
    "cs_bw_filter": "Backwashing Filter",
    "c2_01": "Metered Softener",
    "cs_meter_soft": "Metered Softener",
}

# Known Bluetooth local-name prefixes advertised by Chandler Legacy valves.
VALVE_NAME_PREFIXES: Final[tuple[str, ...]] = ("CS_", "C2_", "CL_")

def _case_variants(prefix: str) -> tuple[str, ...]:
    """Return all case permutations for the provided prefix."""

    variants = {
        "".join(chars)
        for chars in product(
            *(
                (char.lower(), char.upper()) if char.isalpha() else (char,)
                for char in prefix
            )
        )
    }
    return tuple(sorted(variants))


# Bluetooth callback matchers describing the devices we are interested in.
#
# Chandler's firmware appears to emit identifiers starting with ``CS_``,
# ``C2_``, or ``CL_`` (case-insensitive). ``BluetoothCallbackMatcher`` objects
# only allow wildcard matching after the first three characters, so we expand
# the prefixes into their literal case permutations and append ``*`` to match
# the rest of the local name.
VALVE_MATCHERS: Final[tuple[dict[str, str], ...]] = tuple(
    {"local_name": f"{variant}*"}
    for prefix in VALVE_NAME_PREFIXES
    for variant in _case_variants(prefix)
)
