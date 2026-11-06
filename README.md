# Chandler Legacy View Home Assistant Integration

The Chandler Legacy View project is a Home Assistant custom integration that
focuses on water system valves such as water softeners and filtration systems.
The integration listens for Bluetooth advertisements emitted by supported
valves, recognises them by their signatures, and surfaces their presence inside
Home Assistant as entities that can participate in automations or dashboards.

## Current capabilities

* Establishes a Home Assistant config entry via the UI (no YAML required).
* Watches for Bluetooth advertisements that match the expected Chandler valve
  signatures, including both the Bluetooth name prefixes and the Chandler
  manufacturer data identifier.
* Tracks multiple valves simultaneously by their Bluetooth address so each
  device is registered individually in Home Assistant.
* Creates binary sensor entities that indicate whether each recognised valve is
  currently available.
* Extracts the firmware version reported in the advertisement metadata and
  surfaces it as an entity attribute for troubleshooting and diagnostics.
* Classifies the advertisement payload to determine whether the valve reports
  as an Evb019 (firmware < 600) or Evb034 (firmware ≥ 600) and exposes the model
  via device information and entity state attributes.

Availability follows Home Assistant's connectable Bluetooth scanners, including
ESP32 proxies. A valve remains available if another proxy can still reach it.
After a valve or proxy goes offline, Home Assistant's advertisement expiry can
take up to five minutes to report the loss; see the
[Bluetooth availability documentation](https://developers.home-assistant.io/docs/core/bluetooth/api/#subscribing-to-unavailable-callbacks).
A live Bluetooth data session also keeps the valve available if it stops
advertising while connected. If that session ends after advertisements have
expired, its entities become unavailable. A new advertisement restores them
and allows polling to resume.

The **Time of Day** sensor is disabled by default when first registered to avoid
frequent clock updates in Activity and history. Enable it in the entity settings
if you want to display the valve's clock. Existing entities keep their current
enabled or disabled setting; disable an existing Time of Day sensor manually
to stop its updates. The integration still reads the valve clock during normal
dashboard polling when this sensor is disabled.

Firmware and model information are accepted only from a recognized complete
advertisement. Short status packets refresh presence without replacing trusted
device metadata. After an integration reload, firmware may remain unknown until
a complete advertisement arrives; an old incorrect value such as `C0.00` is
cleared. Clock and water-use polling can recover independently when a complete
DeviceList response identifies counter-based authentication. A successful
connection alone does not count as a successful dashboard update.

This repository currently focuses on the scaffolding required for discovery and
entity creation. Additional device metadata, richer entities, diagnostics, and
configuration options will follow as device details become available.

## Installation

Requires Home Assistant 2026.8.0 or later for config-entry-scoped device registry
lookups and parent-device links by registry ID.

1. Copy the `custom_components/chandler_legacy_view` directory into your Home
   Assistant `custom_components` folder.
2. Restart Home Assistant to load the new integration.
3. In Home Assistant, navigate to **Settings → Devices & Services → Add
   Integration** and search for "Chandler Legacy View".
4. Complete the configuration flow to enable Bluetooth-based discovery of
   Chandler valves. The integration uses the factory default valve passcode
   (`1234`) automatically; per-valve overrides can be configured later from the
   integration options. Only one instance of the integration is required.

## Development

### Tests

Use Python 3.14 (validated with 3.14.2). Create a virtual environment,
install one of the requirement sets below, then run `python -m pytest`.
Each target pins its Home Assistant release and that release's declared
Bluetooth and USB integration dependencies.

| Home Assistant target | Test requirements |
| --- | --- |
| 2026.10.4 | `requirements-test.txt` |
| 2026.8.0, the declared minimum | `requirements-test-min.txt` |

The fixtures run on native Windows and initialize HA's real objects
directly, including config entries, platforms, services, state machine,
event bus, registries, and storage restoration. They avoid the upstream
fixture plugin's Unix-only startup runner; no HA validation rules are
reimplemented in local entity or registry substitutes.

Home Assistant configuration and registry storage are confined to the
test-owned `.pytest-tmp` directory, which pytest recreates for each run.
External sockets are blocked. Loopback and Unix sockets are allowed for
asyncio's initial wakeup mechanism; once a real-HA fixture is active,
connections (including loopback), server binds/listeners, and datagrams
are also rejected. Fixture paths outside the owning test's directory are
rejected before HA is constructed. Runtime package installation is off.
No Bluetooth scanner, adapter, proxy, USB watcher, or HA server is started.

Shared fakes supply scanner observations, expiry, and GATT I/O while
production discovery, connection, protocol, and entity code runs normally.
GATT sessions have distinct identities, services, subscriptions, gated
operations, cancellation, and connected/disconnected state. The simulated
valve decodes authentication requests independently of the production
encoder, checks the PIN against the current session's issued challenge,
and withholds protected readings until access is established. Fixed
synthetic wire examples exercise this decoder, including challenge wrap
and rejection of stale requests; they are not live-valve captures. The shared
clock derives HA UTC and local time from one controlled instant and advances
them together with event-loop deadlines, without shortening production
retries or cleanup budgets. Timestamp tests check complete published dates,
times, and offsets across local midnight in UTC and America/Chicago; fixed
start times use the same shared clock, and HA configures the real time zone.

Coverage includes exhaustive advertisement layouts and counter values,
authentication and fresh data, queued old notifications, session ownership,
bounded cancellation, published availability sequences across all four
platforms, sensor defaults and AM/PM readings, enabled/disabled choices
across reload and storage restoration, scoped device metadata and parent
links, and failed/concurrent/cancelled entry teardown. Pure parser cases
remain focused byte-to-result tests. Fake-only classifier outcomes that
the production parser never emits have been replaced by actual rejected
observations; malformed-input parser checks remain.

Partial startup is also covered through the real discovery and connection
managers and HA startup. Empty manufacturer data and logged short packets
are tested both before and after entry setup: presence appears, followed
by authenticated clock and water-use readings, while firmware stays unknown.
The standalone connection fixture preserves existing scanner observations
and does not supplement partial metadata with a complete advertisement.

Persistent refresh coverage turns the real HA switch on, observes successive
water-use state-change events on one still-connected GATT session, and then
turns it off for cleanup. This exposes notification leaks before disconnect
can clear them or a replacement connection can supply the next reading.

Configured authentication runs through real config entries and the
production connection manager. Tests cover custom default PINs, per-valve
override precedence without affecting another valve, and published lockout
and reading recovery after correcting settings through HA's options flow
and automatic reload. Simulated valves retain independently configured PINs.

Narrow failure injection is retained for task-creation failure, platform
unload refusal/cancellation, and unexpected manager cleanup errors. These
are the core scenarios of those tests and cannot be produced by normal
Bluetooth responses. There are no synthetic HA entity/registry classes,
private production-method replacements, or cross-test fixture imports.

Unload failures and cancellation run through HA's config-entry API. A
platform refusal leaves the entry in `FAILED_UNLOAD`; cancellation during
platform unload leaves `UNLOAD_IN_PROGRESS`. Both retain the runtime and
published entities, and HA rejects a subsequent unload attempt. These
tests do not reset entry state or imply that retrying unload is supported.

Validation uses simulated Bluetooth I/O. In particular, a timed-out
disconnect deliberately leaves the simulated link connected; completion
of a cleanup task does not establish physical disconnection or released
proxy capacity. Live adapters, ESP32 proxies, and valve firmware recovery
remain hardware validation gaps. The version matrix above is validated on
Windows; Linux and macOS execution have not been checked in this migration.

* `custom_components/chandler_legacy_view/discovery.py` implements the Bluetooth
  discovery logic.
* `custom_components/chandler_legacy_view/binary_sensor.py` defines the initial
  entity type exposed by the integration.
* Configuration flow strings are located in
  `custom_components/chandler_legacy_view/translations/en.json`.

Contributions are welcome! The goal is to expand the integration to expose the
valve state (e.g. service mode, regeneration cycles) and diagnostics as the
device protocol becomes better understood.

### Local Codex initialization prompt

> Read AGENTS.md and README.md to familiarize yourself with this repository. After performing any work which resulted in uncommitted changes to repository files, provide a commit message for the work. Commit messages should be provided in fenced code blocks for easy copy/paste.
