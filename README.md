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

## Reliability and stale data

Water-system data is only useful if it is current, so the integration is
built to fail loudly rather than quietly keep showing an old reading.

* **Every Bluetooth call is bounded.** A write, notification subscribe or
  unsubscribe, or service lookup that stalls on a dead link times out after
  10 seconds instead of holding the valve's poll lock indefinitely. A whole
  on-demand poll is cut off after 60 seconds and a persistent-session request
  after 30.
* **A watchdog abandons wedged work.** Every 30 seconds each valve is checked.
  A poll or persistent session that outlives a 3 minute hard limit is cancelled
  and replaced even if the Bluetooth stack ignores cancellation, so polling
  always resumes without a restart.
* **Reload and shutdown cannot hang.** Unloading the integration, switching a
  persistent connection off, and shutting Home Assistant down wait a bounded
  time for Bluetooth cleanup and then move on.
* **Stale data is visible.** Dashboard sensors (flow, usage, battery, and the
  rest) become *unavailable* when no fresh dashboard has arrived for too long:
  45 minutes with on-demand polling (three missed 15 minute polls), or eight
  poll intervals (never less than 2 minutes) with a persistent connection. An
  idle valve with stale data is polled again automatically, at most every 2
  minutes.
* **Persistent connection settings survive restarts.** The *Persistent
  Connection* switch and *Persistent Poll Interval* are restored after a restart
  or reload.

Two diagnostic entities per valve support alerting:

| Entity | Meaning |
| --- | --- |
| **Data Stale** (problem) | On when the valve's data has gone stale. Always available, including when the valve is out of range, so an automation can alert on it. |
| **Last Data Update** | How old the data is, in coarse steps: *Within 5 minutes*, *5 to 15 minutes ago*, *15 to 60 minutes ago*, *1 to 6 hours ago*, *6 to 24 hours ago*, *Over a day ago*, or *No data yet*. It changes only when a step is crossed, so a healthy valve writes almost nothing to the recorder even with a persistent connection. Once the data is no longer fresh, the exact time of the last data is in the `last_data_update` attribute. Always available. |

For leak detection, alert on **Data Stale** turning on rather than only on flow
values: a flow sensor that reads zero because polling has silently stopped looks
the same as a quiet pipe unless staleness is monitored. Bluetooth is not a
guaranteed link, so treat valve flow as one signal alongside a dedicated leak
sensor rather than the only one.

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
