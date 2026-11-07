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
Poll Interval**, in seconds (5–300; default 10). Its default and valve overrides
are also in Configure. The existing interval number remains a working local
configuration control, including while the valve is unreachable. Writing it
always saves a valve override. Persistent interval edits apply to the current
session without restarting it. Evb034 notifications are acknowledged promptly
even when publication uses a longer interval.

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

## Reading expiration

Expired Dashboard readings become unavailable
independently of Bluetooth presence. Presence can remain on while a reading
is unavailable. On-demand maximum age is the declared interval plus five
minutes of recovery allowance: 20 minutes by default, or 65 minutes for an
hourly custom schedule. Persistent maximum age is the greater of three
persistent intervals or two minutes (15 minutes at a five-minute interval).

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

Evb034 uses session health for fields with a valid baseline: receiving a valid
field makes that field eligible, and later valid reading updates renew all
eligible fields in the same authenticated session. Fields can first appear
later in a session. A clock needs all required components before eligibility.
Fields never received stay unavailable; their entities and registry enablement
are unchanged. Existing firmware/type/Clack restrictions still apply.

Metadata, ACKs, nulls and invalid data do not renew session health. Silence
uses the same deadlines above. Access loss, receiver failure or a detected
unexpected disconnect invalidates the session baseline, and reconnect starts
with no eligible fields. A completed on-demand snapshot remains available
between reads until its deadline. First availability and recovery publish the
current snapshot immediately, while ordinary updates retain the configured
publication interval.

This is a deliberate policy assumption: other reading updates can keep one
field marked fresh even if that field's producer has stopped. The APK does
not prove a reporting guarantee for each field. See
[Evb034 reporting evidence](evb034-reporting.md) for the decision and remaining
hardware validation.
