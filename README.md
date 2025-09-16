# Chandler Legacy View Home Assistant Integration

The Chandler Legacy View project is a Home Assistant custom integration that
focuses on water system valves such as water softeners and filtration systems.
The integration listens for Bluetooth advertisements emitted by supported
valves, recognises them by their signatures, and surfaces their presence inside
Home Assistant as entities that can participate in automations or dashboards.

## Current capabilities

* Establishes a Home Assistant config entry via the UI (no YAML required).
* Watches for Bluetooth advertisements that match the expected Chandler valve
  signatures.
* Creates binary sensor entities that indicate whether each recognised valve is
  currently available.

This repository currently focuses on the scaffolding required for discovery and
entity creation. Additional device metadata, richer entities, diagnostics, and
configuration options will follow as device details become available.

## Installation

1. Copy the `custom_components/chandler_legacy_view` directory into your Home
   Assistant `custom_components` folder.
2. Restart Home Assistant to load the new integration.
3. In Home Assistant, navigate to **Settings → Devices & Services → Add
   Integration** and search for "Chandler Legacy View".
4. Complete the configuration flow to enable Bluetooth-based discovery of
   Chandler valves. Only one instance of the integration is required.

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
