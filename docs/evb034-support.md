# Evb034 BLE support

The reference is Water System / Legacy View 6.18.2, package
`com.chandler.systems.csi`, version code 135. Its extracted Smali is kept in
ignored local artifacts. Evidence below describes simulated Bluetooth I/O;
actual C6.x valves, adapters and proxies still require acceptance in #61.

## Delivery sequence

1. #57: identify C6.x valves and exchange CRC16-framed JSON packets over the
   existing BLE profiles, with bounded retries and connection-owned state.
2. #58: authenticate from fresh identification counters and preserve lockout,
   reconnect, cancellation and persistent/on-demand connection behavior.
3. #59: map C6.x data to the existing HA entities, preserving IDs, units,
   creation rules, settings and missing-value behavior.
4. Correct the notification lifecycle discovered during the #60 review in
   the additional corrective commit authorized by the user.
5. #60: apply Clack/EBX044 metadata, type mappings and conversions, with a
   support record that distinguishes automated coverage from hardware proof.
6. #61: obtain paired app/HA values and live connection/recovery evidence
   from users with the relevant valves and host/proxy combinations.

## Packet reference

`CsBleViewModel.onBleDeviceFullConnected` sends `EA` before packet exchanges.
`CsBleQueueProcessor.chunkPacket/addChunkToQueue` adds first (`80`) and last
(`40`) flags and a little-endian CRC16 to each chunk. The available ATT value
length is the negotiated MTU minus three, as established by
`CsBleLeManager.configureConnection$lambda$7`. Each frame consumes another
three bytes for the Chandler header and CRC. `CsCrc16$Companion.compute` uses
CCITT polynomial `1021` and initial state `FFFF`.

`processStatusPacket`, `writeAckNakPacket`, `writeKeepAlivePacket`, and
`writeTimeoutPacket` define ACK `CC`, NAK `C8`, keepalive `E0`/`F0`, and
timeout query/ack `C2`/`C3`. `processDataPacket` assembles a JSON object with
a 2048-byte receive limit. The Evb019 DeviceList and Dashboard request
payloads are not sent on an identified C6.x connection. Identification and
readings arrive as notification updates after EA. The app skips the Evb019
polling loops on C6.x (`CsDeviceListFragment.lambda$writeData$8` and
`CsDashboardFragment.onViewCreated`). Authenticated configuration JSON uses
the framed outbound queue.

Identification alone is not a successful Dashboard read. Packet assembly and
notification callbacks belong to one connection; cancelled work cannot supply
identification to a replacement connection. Tests cover CRC/JSON rejection,
fragmentation, coalescing, MTU sizes, bounded ACK/NAK/timeouts and cancellation
alongside all Evb019 regressions.

## Authentication and recovery

`CsDataBufferEvb034$Companion.getPasswordBuffer` uses a random buffer of
`min(20, available ATT bytes)`, CRC8 polynomial/seed/nonce at offsets 1/3/5,
and encrypted thousands/ones/hundreds/tens digits at offsets 7/9/11/13.
`CsBleViewModel.write` obtains the counter from the current packet's `dlc`,
not from the advertisement or a previous connection. `CsBlePacketAuthState`
defines unknown `0`, unauthenticated `1`, and authenticated `2`.
`CsBleViewModel.write` sends the initial PIN directly while unauthenticated;
framing is reserved for authenticated configuration writes.
`CsDeviceListFragment.onPacketUpdated` permits two PIN attempts using fresh
counter evidence from the current connection. Replies delivered before a
write cannot count as its credential response.

The Authentication Lockout switch is available on Evb034 because access is
reported in packet `as`, independently of the advertisement's status bits.
Only an explicit credential rejection locks out retries. Missing, malformed
or unknown authentication replies remain retryable; clearing lockout or
changing configured credentials permits a new attempt. Reconnect always
identifies and authenticates again. A connection-owned receiver ACKs
notifications continuously, including while HA waits for its configured
publication interval. Ordinary deltas coalesce within bounded storage;
authentication transitions stay ordered. An idle healthy stream preserves
its readings and last-success time. Access loss or receiver failure wakes
cleanup immediately rather than waiting up to the 300-second HA interval.
Teardown invalidates callbacks and drains the receiver before disconnecting.

## Existing entity readings

`CsBlePacket$Companion.updatePacket/getFloatDivisor` divides present flow
(`gpf`) and daily peak flow (`dpfd`) by 100 on all C6.x versions. Usage
(`dwu`) and remaining water (`dtgr`) use divisor 1 before C6.11 and 100 from
C6.11 onward; remaining water is truncated to integer gallons. Hardness
(`dwh`) is unscaled. `CsDashboardFragment.updateRegenOptionsEvb034` uses
current day countdown `dcdo`, independently of configured frequency `ddo`.

`CsBlePacket.getFullTimeString` changes from hours plus AM/PM to a 24-hour
field at C6.09. This boundary is distinct from the C6.11 volume boundary.
Battery `dbl` is millivolts, converted with the reference's piecewise curve.
`processPacketEvb034` enables battery sampling with `gbre=1` on non-Clack
valves when it is not already enabled.

Absent or invalid readings remain unknown. Null/omitted updates preserve
values already received in the same session, matching the reference's packet
updates; a replacement session does not borrow missing readings from its
predecessor. Validated identification can recover model, type, firmware,
series and concatenated hexadecimal serial fields `dlsa`/`dlsb` after partial
startup, preserving the address-based HA device and entity identities.

## Clack and EBX044 variants

`CsMainCoreActivity` copies `dlIsClackValve`, `dlIsTwinValve` and the mapped
valve type into the Dashboard model after connecting. Confirmed `dlic` and
`dli` therefore govern availability, rather than the discovery name alone.
The packet flag also controls `CsBlePacket$Companion.getFloatDivisor` and
`CsBlePacket.getFullTimeString`: Clack always uses hundredths for usage and
remaining volume and a 24-hour clock. The battery row and battery-sampling
write are excluded for Clack. `dtgr` follows JVM float division and integer
truncation, including large-counter rounding and signed-integer saturation.

`CsValveType$Companion.fromValveType` maps Clack types 1/4/6/8 to metered
softener, 2/5/7/9 to backwashing filter, and 3 to Clack aeration. Standard
commercial metered type 17 and all twins use metered applicability. Unknown
type/series codes remain explicit diagnostics and do not invent a new model.
`dlcf` is a byte array decoded as UTF-8 by `arrayToString`; expose that
controller firmware separately from the numeric valve firmware.

`CsBleValveDataEvb034.parsePayload` interprets the same series byte through
both `CsValveSeriesVersionEvb034` and `CsValveSeriesVersionEbx044`. EBX044
is not inferred as a separate transport from a branding or firmware prefix.
Keep the existing Evb034/EBX044 series diagnostics; a BLE valve with these
reported fields uses the packet session above. RFCOMM-only valves remain
outside implementation scope and are covered by investigation #97.

## Support evidence

| Combination | Automated evidence | Hardware evidence |
| --- | --- | --- |
| Existing Evb019, including C4.18/C4.19/C4.20+ and D1 twins | Full regression suite retained | Existing reports; no new live validation in this work |
| C6.00–6.08 | Packet/authentication, legacy clock flag, unscaled volumes | Needed in #61 |
| C6.09–6.10 | 24-hour clock and unscaled volumes | Needed in #61 |
| C6.11+ including the C6.18 report scenario | 24-hour clock, hundredths volume, present/peak flow scaling | Needed in #61 |
| Standard/commercial/twin packet flags | HA readings, type/applicability transitions, stable identities | Needed in #61 |
| Clack BLE types 1–9 | Hundredths volume, 24-hour clock, battery exclusion, type mappings | Needed in #61 |
| Evb034/EBX044 series 1–7 | Shared packet path, series diagnostics, unknown-series handling | Needed in #61 |

Tests use real HA objects with simulated scanner/GATT I/O on HA 2026.8.0
and 2026.10.4 under Windows/Python 3.14.2. They do not establish physical
disconnects, released ESPHome proxy slots, MTU negotiation with actual valves,
or phone-app handoff. Collect paired app/HA readings, exact versions and
transport-boundary recovery/release observations from future user reports.

## Unexpected situations and open questions

The #60 reference review found that the original #57–59 session and peer
incorrectly assumed request/reply DeviceList and Dashboard polling.
`CsDeviceListFragment.lambda$writeData$8` skips the DeviceList write path
for `is600Series()`; `CsDashboardFragment.onViewCreated` processes the
C6.x packet stream instead of starting `writeData`. Those earlier passing
tests established encoding and conversion coverage but did not establish a
faithful C6.x lifecycle.

The user authorized an additional corrective protocol commit before #60.
The correction receives identification after EA, sends the initial PIN
directly, reserves framed writes for authenticated configuration JSON, and
receives ongoing reading deltas without tt/uu polls. A continuously running
owned receiver acknowledges frames independently of HA publication timing.
The simulated valve initiates updates and requires per-frame ACKs within
three seconds; it no longer manufactures replies to invented read requests.

Regression checks cover a 300-second HA interval with continuous ACKs, queue
overflow/access-loss cleanup, bounded framing/command failures, stale
pre-write authentication states, and cancellation/unload with pending
notifications. Simulated cleanup is distinct from physical release evidence.

The corrected lifecycle is recorded in commit `86af040`. With #60 variant
interpretation and support records, the repository implements the existing
entity coverage planned in #57–60. The remaining step is #61 hardware evidence
from future user reports. RFCOMM remains excluded. The unsequenced wire
limitation below remains unresolved by design; hardware behavior is unverified
rather than inferred from simulated tests.

## Protocol limitations and remaining evidence

The reference's chunks have no sequence number, message ID or payload length.
First/last markers, CRC, JSON validation and request ownership detect malformed
or overlapping deliveries, but cannot prove that an otherwise valid JSON
value was never duplicated or delayed by the valve. Do not invent sequence
fields or claim stronger replay detection than the wire protocol provides.
Hardware reports remain necessary for actual notification ordering, connection
release, proxy slot reuse and firmware-specific behavior.
