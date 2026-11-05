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

DEFAULT_VALVE_PASSCODE: Final = "1234"

DOMAIN: Final = "chandler_legacy_view"
PLATFORMS: Final[list[Platform]] = [
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
]

# Persistent connection configuration
DEFAULT_PERSISTENT_POLL_INTERVAL_SECONDS: Final = 10.0
MIN_PERSISTENT_POLL_INTERVAL_SECONDS: Final = 5.0
MAX_PERSISTENT_POLL_INTERVAL_SECONDS: Final = 300.0

# Storage keys used inside ``hass.data``
DATA_DISCOVERY_MANAGER: Final = "discovery_manager"
DATA_CONNECTION_MANAGER: Final = "connection_manager"
DATA_DISCOVERY_DEVICE_ID: Final = "discovery_device_id"

# Polling configuration for on-demand Bluetooth connections
CONNECTION_POLL_INTERVAL: Final = timedelta(minutes=15)
CONNECTION_MIN_RETRY_INTERVAL: Final = timedelta(seconds=30)
CONNECTION_TIMEOUT_SECONDS: Final = 20

# Reliability limits. A stalled Bluetooth link must never be able to hold a
# valve's poll lock or its persistent session forever, so every Bluetooth call
# is bounded and a watchdog abandons work that outlives these limits even if
# the underlying call ignores cancellation.
#
# Longest a single GATT operation (write, notify, service lookup) may take.
BLE_OPERATION_TIMEOUT_SECONDS: Final = 10
# Longest the post-connect exchange of one on-demand poll may take.
POLL_FETCH_TIMEOUT_SECONDS: Final = 60
# Longest one persistent-session dashboard exchange may take.
PERSISTENT_REQUEST_TIMEOUT_SECONDS: Final = 30
# A poll or session still running this long is abandoned by the watchdog.
POLL_HARD_LIMIT_SECONDS: Final = 180
# Longest unload or reload waits for in-flight Bluetooth work before abandoning it.
UNLOAD_TIMEOUT_SECONDS: Final = 15
# Gap before a persistent session reconnects after the link drops.
PERSISTENT_RECONNECT_DELAY_SECONDS: Final = 5
# How often the watchdog checks every valve for stuck work and stale data.
WATCHDOG_INTERVAL: Final = timedelta(seconds=30)
# Minimum gap between self-heal polls triggered by stale data.
HEAL_POLL_MIN_INTERVAL_SECONDS: Final = 120

# The Last Data Update sensor reports how old the data is in coarse steps rather
# than a timestamp. It only changes (and so only writes to the recorder) when the
# age crosses a step, so a healthy valve writes almost nothing, even with a
# persistent connection delivering data every few seconds. Staleness itself is
# judged separately, from the connection's own timing.
DATA_AGE_TIERS: Final = (
    (5 * 60, "Within 5 minutes"),
    (15 * 60, "5 to 15 minutes ago"),
    (60 * 60, "15 to 60 minutes ago"),
    (6 * 60 * 60, "1 to 6 hours ago"),
    (24 * 60 * 60, "6 to 24 hours ago"),
)
DATA_AGE_FRESH: Final = DATA_AGE_TIERS[0][1]
DATA_AGE_OLDEST: Final = "Over a day ago"
DATA_AGE_NONE: Final = "No data yet"

# Data older than this is reported as stale and its sensors become unavailable.
# On-demand polling runs every 15 minutes, so three missed polls are stale.
STALE_AFTER_ON_DEMAND_SECONDS: Final = 45 * 60
# With a persistent connection the limit follows the configured poll interval.
STALE_AFTER_PERSISTENT_MIN_SECONDS: Final = 120
STALE_AFTER_PERSISTENT_INTERVALS: Final = 8

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
