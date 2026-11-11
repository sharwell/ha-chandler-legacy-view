# Display-off configuration

The Display Off configuration switch sets one valve preference. On means the
display is off; off means the display is on. It does not start regeneration,
change water controls, or rewrite the other settings carried in status bytes.
Its identity is the valve address plus `_display_off`, independent of its name.

Entity creation waits for confirmed support: firmware C4.13 or later, or a
twin valve. Connected identification can establish support after an incomplete
advertisement. Unsupported non-twin C4.10–C4.12 valves do not acquire the
switch just because their Dashboard exposes the raw flag. If later identity
rules a valve out, an existing switch becomes unavailable. Newer BLE and
Clack valves use the same reference presentation gate, within the protocol
families already supported by the integration. Missing newer display values
remain unavailable until explicitly received; they are never interpreted as
display-on defaults.

## Reference evidence

Reference: Water System / Legacy View 6.18.2, package
`com.chandler.systems.csi`, version code 135. Paths below are relative to the
ignored extracted Smali root's `com/chandlersystemsinc` directory.

| Reference method | Behavior used |
| --- | --- |
| `signatureappcore/viewmodels/CsAdvancedSettingsViewModel.setupValveOptions` | Both display-option branches test firmware `>= 413` or the twin flag. This differs from the C4.10 raw Dashboard decoding threshold. |
| `signatureappcore/fragments/CsAdvancedSettingsFragment.setInitialDataEvb019` and `setDisplayOffStateEvb034` | Active display-off means the display is off; inactive means the display is on. |
| `signaturebluetooth/core/CsDataBufferEvb019$Companion.getSetDisplayOnOffBuffer` and `getFilledBuffer` | Fill a 20-byte Advanced Settings packet with `0x76`; put selector `0x47` at byte 13 and Active/Inactive (`1`/`0`) at byte 14. The command selects only this field. |
| `signaturebluetooth/models/evb019/CsDashboardPacket.parsePacket1` | Dashboard packet 1 byte 18 bit `0x10` reports display-off state, independently of the other bits. |
| `signaturebluetooth/core/CsDataBufferEvb034$Companion.setDisplayState` and `signaturebluetooth/enums/CsBlePacketKey` | Send integer key `asdo` with value `1` or `0` using the authenticated framed command queue. |
| `signaturebluetooth/models/evb034/CsBlePacket$Companion.updatePacket` | Explicit `asdo` updates supply display-off state. Omitted/null values are not new confirmations. |

## Command lifecycle

Per-valve configuration requests run sequentially. An in-progress poll finishes
first; an existing persistent session is retired before a fresh command
session is opened. This uses the existing connection ownership, cooldown,
authentication, I/O deadlines and cleanup quarantine. A successful command
session resumes persistent monitoring when requested; no monitoring preference
is changed. This intentionally trades a reconnect for simple ownership of the
first user-facing device-setting transaction.

The initial authenticated readings must succeed, and freshly learned identity
must still permit the setting. Evb019 then sends the field-specific command
and requests a complete Dashboard readback. Evb034 requires an explicit valid
`asdo` received after command submission. ACKs and unrelated streaming fields
cannot confirm adoption. Per-field receipt timestamps prevent a coalesced old
`asdo` from being mistaken for command readback.

A differing value leaves the command pending, matching the app's
`checkUpdateEvb019` and `setDisplayOffStateEvb034` behavior. Confirmation has
a nine-second deadline: Evb034 waits for a matching explicit update, while
Evb019 requests Dashboard readback again with a one-second pause between
reads. Only read requests repeat; the setting command is sent once. If the
value never matches, the operation times out with an unconfirmed outcome.

Only received valve values update the switch. Rejection, lost readback and
write timeout raise errors without publishing an assumed requested value.
An unconfirmed command makes the display control unavailable until a later
read supplies its state again.
After a lost reply the physical outcome may be unknown: the command is not
automatically replayed. A later ordinary read can reconcile the actual state.
Cancellation and unload retire the owned work; a cancellation-resistant
backend remains quarantined until its pending I/O and disconnect settle.

## Validation and remaining hardware checks

Automated tests use production connection/discovery managers, protocol code,
Home Assistant entities, state events, service calls and registries. The shared
fake is at the scanner/GATT boundary. Tests cover the C4.10–C4.13 boundary,
twins, C6.00/C6.18 and Clack identity, both setting directions, unchanged
settings, unrelated status bits, persistent/on-demand operation, reload,
external changes, credential rejection, missing readback, write failure,
deadlines, concurrent requests, unload and cancellation-resistant I/O.

This is simulated Bluetooth evidence. Live display behavior, physical
disconnects, ESPHome proxy-slot release and real firmware response timing
still need hardware confirmation. For each tested combination, record the
valve model/type, firmware, HA version, adapter or proxy and ESPHome versions.

1. Compare the initial switch value with Legacy View and the physical display.
2. Turn Display Off on, then off, verifying the physical display and subsequent
   HA/app readback each time. Confirm other configuration and water operation
   remain unchanged.
3. Repeat in persistent and on-demand modes, then reload and verify the valve's
   setting is reread. Change it in the phone app and confirm a later HA read
   reflects the change.
4. Check recovery after an interrupted exchange and phone-app handoff. Record
   uncertain outcomes separately from explicit rejection, and obtain physical
   connection/proxy-capacity evidence at the relevant hardware boundary.
