# Polling and refreshing valve readings

With **Persistent Connection** off, Home Assistant reads a valve every 15
minutes by default and disconnects between reads. Advertisements still update
presence and broadcast status; they do not trigger extra reads while the
readings are current. Startup and recovery can request an earlier read.

Open **Settings → Devices & services → Chandler Legacy View → Configure**.
Set **Default on-demand interval** in minutes (1–1440). To override one valve,
select it and enter **Override on-demand interval**. To return to inheritance,
clear that field and select **Use integration default for on-demand interval**.
Blank override fields retain their existing values. Current overrides appear
in the options description. Defaults and overrides survive reloads/restarts;
editing them recalculates due times from the last validated reading.

Persistent mode retains one connection and uses its separate **Persistent
Poll Interval**, in seconds (1–300; default 10). Its default and valve overrides
are also in Configure. The existing interval number remains a working local
configuration control, including while the valve is unreachable. Writing it
always saves a valve override. Persistent interval edits apply to the current
session without restarting it. Evb019 waits this interval between reading
exchanges; response time adds to the elapsed time between updates.
Evb034 notifications are acknowledged promptly even when publication uses a
longer interval. For Evb034, the persistent interval controls Home Assistant
publication cadence, not the valve's own reporting interval.

## Upgrading saved monitoring preferences

Persistent mode and interval choices are stored in entry options independently
of whether their control entities are enabled. Monitoring preference edits do
not reload the integration; changing persistent mode starts or stops its
session as requested. Credential changes retain the reload behavior.

Older releases did not persist these controls. During the one-time migration,
the integration can recover a saved persistent **on** state or a valid,
non-default interval from Home Assistant's current or restored entity states.
Existing entry options take precedence. Old **off** or **10-second** states,
and unknown/unavailable states, cannot establish a deliberate selection.
Choices whose history was already lost cannot be reconstructed; check the
settings after upgrading.

## Refresh readings

Use **Developer tools → Actions → Home Assistant: Update entity**
(`homeassistant.update_entity`) and select one enabled valve reading sensor,
such as Water Usage Today. This refreshes the shared readings for that valve.
Selecting several sensors for the same valve coalesces into one refresh.
No refresh button or new interval number is required.

Manual refresh bypasses the normal on-demand interval, but waits for the
30-second connection cooldown when necessary. It joins an ongoing read or
uses an existing persistent session. An Evb034 refresh consumes validated
stream notifications; it does not send an Evb019 read command. Buffered
notifications retain their original receipt times. An unreachable valve,
authentication lockout, unconfirmed cleanup or missing reading reports an
action error rather than a successful refresh. Cancelling an individual
caller does not abandon shared Bluetooth work; integration unload cancels
and cleans up the owned work.

## Custom polling automation

For your own on-demand schedule, keep Persistent Connection **off**. In the
integration entry's menu, open **System options** and turn **Enable polling
for changes** off. This suspends the integration's automatic on-demand reads,
including startup, advertisements, due timers and deferred retries. Manual
updates still work. The setting does not stop an explicitly enabled persistent
session; turn off that valve's Persistent Connection switch as well.

Set the expected on-demand interval in Configure to match the custom schedule.
For example, declare **60 minutes** for hourly polling. This interval remains
the freshness expectation even when automatic polling is disabled; running
an automation alone does not confirm fresh data.

Replace the sensor ID below with your enabled valve reading sensor. This is
a complete `configuration.yaml` example for hourly polling:

```yaml
automation:
  - id: poll_chandler_hourly
    alias: Poll Chandler readings hourly
    triggers:
      - trigger: time_pattern
        minutes: "0"
    actions:
      - action: homeassistant.update_entity
        target:
          entity_id: sensor.water_softener_water_usage_today
    mode: single
```

## Manual refresh script

This complete `configuration.yaml` example creates a manually runnable script
that can also be placed on a dashboard. Replace the sensor ID in the same way:

```yaml
script:
  refresh_chandler_readings:
    alias: Refresh Chandler readings
    sequence:
      - action: homeassistant.update_entity
        target:
          entity_id: sensor.water_softener_water_usage_today
    mode: single
```

The explicit entity references let Home Assistant associate these automations
and scripts with the valve. Open the device page's **Related** section to find
them. Entity IDs built dynamically in templates may not be discoverable there.

## Transport recovery

Bluetooth reads and cleanup have bounded waits. If a backend does not confirm
release, Home Assistant logs the failure and retains the old connection in
quarantine. Replacement setup retries until release is confirmed. Consult the
Bluetooth adapter/proxy logs when this persists. A completed integration unload
does not itself prove that a physical connection or proxy slot was released.

### Operation and cleanup budgets

These are integration wait limits, in seconds; they do not guarantee that a
stalled Bluetooth backend has stopped its external work when the wait ends.

| Operation | Wait limit |
| --- | --- |
| Connection establishment | 20 seconds |
| Individual GATT operation | 10 seconds |
| Complete initial exchange, including authentication attempts | 60 seconds |
| Persistent Evb019 reading exchange | 30 seconds |
| Evb034 wire write/ACK or partial-frame assembly deadline | 3 seconds |
| Request-notification unsubscribe allowance | 5 seconds |
| Reset or Evb034 stream-close cleanup allowance | 5 seconds |
| Disconnect allowance | 30 seconds |
| Total caller wait for persistent switch-off or unload cleanup | 45 seconds |

A complete initial attempt has a 120-second budget: connection, exchange,
request unsubscribe, reset/stream close, and disconnect. Each cleanup phase
has its own allowance; a reset failure does not consume the disconnect budget.
The 45-second lifecycle wait is a separate caller deadline, not a new lifetime
limit for a healthy persistent session. Repeated caller cancellation does not
restart that deadline or relinquish unfinished external work.

Switching persistent mode off retains the **off** preference even when the
action reports unconfirmed cleanup. Logical entry unload can likewise finish
with the old owner quarantined. Late callbacks from the old session cannot
update replacement-session readings; replacement setup waits for old I/O and
disconnect to settle.
Manual refresh reports unsuccessful reads to the calling action/automation;
it does not treat a completed wait as a successful reading.

## Reading expiration

Expired Dashboard readings become unavailable
independently of Bluetooth presence. Presence can remain on while a reading
is unavailable. On-demand maximum age is the declared interval plus five
minutes of recovery allowance: 20 minutes by default, or 65 minutes for an
hourly custom schedule. Persistent maximum age is the greater of three
persistent intervals or two minutes (15 minutes at a five-minute interval).

The exact maximum-age calculations use seconds:

| Mode | Maximum confirmation age |
| --- | --- |
| On-demand | `I + max(300, 2 * A + C + 15)` |
| Persistent | `max(3 * P, 120)` |

`I` is the expected on-demand interval, `P` the persistent interval, `A` the
complete-attempt budget (120 seconds), and `C` the connection cooldown (30
seconds). These budgets currently give a 300-second on-demand recovery
allowance. They bound integration waits without proving physical release.

A due read and its permitted retry can make readings unavailable sooner if
both fail. Failed early manual refreshes do not expire current readings or
start extra automatic retries during their normal freshness window. An Evb019
reading without an initial value remains unknown while acquisition is pending,
then becomes unavailable after two due failures or five minutes without
confirmation.

A valid complete authenticated Evb019 Dashboard response confirms its
applicable readings, including unchanged values and zero. Advertisements,
attempts and incomplete or invalid replies do not renew them. Interval/mode
edits recalculate from the original confirmation time; increasing an interval
cannot revive an expired value. New valid data restores the value and
availability together. Firmware/type/Clack applicability still takes precedence.

### Evb034 session-health policy

Evb034 uses an integration policy assumption: a valid received field establishes
eligibility, and later valid authenticated reading activity renews every
eligible field in that session. **One field's producer can stop updating while
other reading traffic keeps its retained value marked fresh.** This detects
loss of session reading activity, not every individual producer failure.

- A baseline requires an explicit valid authenticated value; zero and unchanged
  values are valid. Session initialization must finish before the baseline can
  be published as available. A field may first appear later in the session;
  fields never received remain unavailable.
- A clock needs valid hour and minute components, plus AM/PM on non-Clack
  C6.00–6.08. C6.09+ and Clack use a 24-hour clock. Required components
  must come from the current session; their original receipt times survive
  buffering and coalescing.
- A complete validated packet with a usable known reading component renews
  eligible baselines. Metadata, ACKs, keepalives, null-only packets, and invalid
  data do not. A null or invalid field does not erase an existing valid baseline.
- Silence uses the maximum-age and due-failure rules above. Access loss,
  receiver failure, or a detected unexpected disconnect invalidates eligibility;
  a new authenticated connection starts with no eligible fields. Late old
  callbacks cannot establish a baseline in its replacement.
- A completed on-demand read retains its snapshot between connections until
  expiration; deliberate disconnect does not immediately expire it. Stopping
  a healthy persistent session first publishes already-received buffered
  readings with their original confirmation times.
- Receipt and HA publication are separate. First availability and recovery
  publish the current snapshot and availability together; ordinary updates
  retain the configured publication interval. Republishing retained values
  cannot manufacture a new receipt time or revive an expired baseline.
- Eligibility affects availability, not entity creation or registry enablement.
  Existing firmware/type/Clack restrictions still apply.

The reviewed APK establishes delta merging and null handling, not guaranteed
per-field report intervals or change-only producer health. These firmware
guarantees and physical adapter/proxy recovery remain unverified by the
simulated regression checks. [The #105 reporting record](https://github.com/sharwell/ha-chandler-legacy-view/issues/105#issuecomment-6960677235)
holds the reporting investigation, selected decision, and remaining capture
requirements.

Historical implementation decisions and original validation results are
preserved in [the #106 completion record](https://github.com/sharwell/ha-chandler-legacy-view/issues/106#issuecomment-6960379940).
