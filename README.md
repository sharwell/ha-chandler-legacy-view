# Chandler Legacy View Home Assistant Integration

Chandler Legacy View is a Home Assistant custom integration for Bluetooth
water-system valves, including water softeners and filtration systems. It
discovers supported Evb019 and Evb034 valves, exposes their readings, and
provides monitoring and display controls. One integration entry tracks
multiple valves by Bluetooth address.

## Current capabilities

| Capability | Behavior |
| --- | --- |
| Discovery and presence | Recognizes Chandler Bluetooth names/manufacturer data, registers each valve separately, and tracks reachability through connectable scanners or an active session. |
| Dashboard readings | Exposes flow, daily usage/peak flow, hardness, remaining water, days until regeneration/backwash, battery, and valve time where applicable. Model/type/Clack restrictions and missing-data/freshness rules govern availability. |
| Regeneration and backwash status | Shows whether the cycle is active, the confirmed step, valve movement, and time left in the current step where reported. Applicable valves also show brine soak or aeration status. |
| Regeneration and backwash actions | Start now, advance an eligible running cycle, or request a cycle at the next scheduled time where supported. See [cycle controls](docs/polling.md#manual-cycle-controls). |
| Monitoring | Reads on demand every 15 minutes by default, supports manual refresh, and offers optional persistent monitoring with saved entry defaults and per-valve overrides. |
| Configuration controls | Provides Persistent Connection, Persistent Poll Interval, Authentication Lockout where applicable, and a confirmed Display Off setting on supported valves. |
| Device metadata | Reports model, firmware, and available type/variant diagnostics without requiring YAML configuration. |

Evb034 support follows the reviewed Legacy View protocol and has automated
coverage across its conversion families and Clack variants. Physical C6.x
behavior and adapter/proxy recovery still need hardware evidence; see the
[protocol reference](docs/evb034-support.md). Retained Evb034 readings use a
[session-health assumption](docs/polling.md#evb034-session-health-policy), which
cannot detect every individual field producer stopping while other reading
updates continue.

## Availability and refreshing readings

Availability follows Home Assistant's connectable Bluetooth scanners, including
ESP32 proxies. A valve remains available if another proxy can still reach it.
After a valve or proxy goes offline, Home Assistant's advertisement expiry can
take up to five minutes to report the loss; see the
[Bluetooth availability documentation](https://developers.home-assistant.io/docs/core/bluetooth/api/#subscribing-to-unavailable-callbacks).
A live Bluetooth data session also keeps the valve available if it stops
advertising while connected. If that session ends after advertisements have
expired, its entities become unavailable. A new advertisement restores them
and allows polling to resume.

Bluetooth presence and reading freshness are separate. An available valve can
have expired or never-received readings. See [Polling and refreshing
readings](docs/polling.md) for interval settings, manual refresh, custom
automations, expiration, and recovery behavior.

The **Time of Day** sensor is disabled by default when first registered to avoid
frequent clock updates in Activity and history. Enable it in the entity settings
if you want to display the valve's clock. Existing entities keep their current
enabled or disabled setting; disable an existing Time of Day sensor manually
to stop its updates. The integration still reads the valve clock during normal
dashboard polling when this sensor is disabled.

## Display Off control

The **Display Off** configuration switch is available on supported C4.13+
valves and twins, including supported newer BLE/Clack variants. Turning the
switch **on blanks the valve's display**; turning it **off restores the
display**. C4.10–C4.12 non-twin valves do not get this control. The switch
reports confirmed valve data and becomes unavailable when that data expires
or authentication is locked out. Merely loading the integration never sends
a display-setting command.

Changes wait for active reads and connection cleanup, then use a fresh
authenticated connection. Unsent changes coalesce to the latest value;
already-sent changes require readback. A failed or unconfirmed command reports
an error and is not automatically repeated. See
[display-control reference](docs/display-control.md) for batching, cooldown,
persistent recovery, protocol details, and validation limits.

## Firmware and advertisement updates

Short status advertisements refresh presence without replacing trusted
firmware metadata. After reload, firmware can remain unknown until complete
identification arrives; an old incorrect value such as `C0.00` is cleared.
A successful connection alone does not count as a successful Dashboard update.

For Evb019, a complete validated DeviceList response can resolve the session's
firmware and authentication format, allowing clock/water-use polling to recover
after incomplete advertisements. Advertisement and device-registry firmware
still wait for a complete advertisement. Evb034 instead publishes validated
packet identity, including model and firmware, to the device registry even
after a partial advertisement.

On Home Assistant versions offering per-device advertisement callbacks
(2026.10 onward), complete raw reports from Home Assistant's preferred
connectable scanner can refresh valve status and the advertised clock even
when Home Assistant's merged manufacturer data appears unchanged. Discovery,
availability, and versions without this API retain the existing callback path;
the minimum Home Assistant version remains 2026.8.0. Duplicate raw reports are
ignored, and subscriptions and pending updates are removed on unload.
Short reports still preserve the last accepted status, and separate reports
are not joined together. Recovery on the C4.40 valves reported in issue #52
still needs live validation; simulated Bluetooth checks establish the callback
behavior, not the affected hardware's delivery pattern.

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

Use Python 3.14 (validated with 3.14.2). Create and activate a virtual
environment, then choose a requirements file for the HA version to test:

| Home Assistant target | Test requirements |
| --- | --- |
| 2026.11.1 | `requirements-test.txt` |
| 2026.8.0, the declared minimum | `requirements-test-min.txt` |

Install the selected requirements and run the suite, for example:

```console
python -m pip install -r requirements-test.txt
python -m pytest
```

The tests exercise production integration code with real HA objects and
Bleak data objects. Bluetooth scanner and GATT I/O are simulated, external
I/O is blocked, and HA storage uses per-test temporary directories.

Recorded local validation covers both HA targets on Windows. CI is configured
to run both targets on Ubuntu and Windows; macOS is outside that matrix.
Simulated Bluetooth I/O does not establish physical disconnects, released
proxy capacity, or live hardware recovery.

* `custom_components/chandler_legacy_view/discovery.py` implements the Bluetooth
  discovery logic.
* `custom_components/chandler_legacy_view/connection.py` owns reads, setting
  commands, session recovery, and their cleanup.
* The `binary_sensor.py`, `sensor.py`, `number.py`, `switch.py`, and
  `button.py` platforms expose presence, readings, controls, and actions.
* Configuration flow strings are located in
  `custom_components/chandler_legacy_view/translations/en.json`.

Use [GitHub Issues](https://github.com/sharwell/ha-chandler-legacy-view/issues)
for future scope and work tracking. The documents under `docs/` explain current
use, protocol decisions, and the evidence still needed for hardware acceptance.

### Local Codex initialization prompt

> Read AGENTS.md and README.md to familiarize yourself with this repository.
