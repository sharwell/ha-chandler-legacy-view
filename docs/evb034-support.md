# Evb034 BLE protocol

This reference describes the implemented BLE packet sessions, authentication,
reading conversions, and Clack/EBX044 variant rules for Evb034/C6.x valves.
For operation and freshness policy, use [Polling and refreshing readings](polling.md)
and its [Evb034 session-health policy](polling.md#evb034-session-health-policy).

The protocol reference is Water System / Legacy View 6.18.2, package
`com.chandler.systems.csi`, version code 135. Method names below refer to its
reviewed Smali. Matching the reference and passing simulated Bluetooth checks
do not establish live firmware behavior, physical release, or proxy capacity.
Hardware evidence is tracked in [#61](https://github.com/sharwell/ha-chandler-legacy-view/issues/61).

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

`processStatusPacket` replies C3 to C2 and C2 to C3. Its
`resetCurrentEntry`/`startProcessor$lambda$0` path restarts the whole queued
message, not just the interrupted chunk, for up to two timeout-control resets.
The timeout count persists across commands in the connection-owned processor.
An exhausted command is dropped; an ordinary later command can still succeed.
`restartTimeout$1.run` reports a three-second local timeout as failure; it does
not originate a C2 query.

Identification alone is not a successful Dashboard read. Packet assembly and
notification callbacks belong to one connection; cancelled work cannot supply
identification to a replacement connection. Each notification is one CRC
frame, as in `processDataPacket`. Validate the complete ATT value rather than
searching for a shorter CRC-valid prefix; a bad short frame immediately receives
NAK without discarding earlier good chunks. Splitting or joining ATT notification
values is not a supported transport behavior.

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
null fields are ignored before coalescing and access checks, and real
authentication transitions stay ordered. Waiting for the publication interval
does not manufacture new confirmations or advance last-success time. Silent
streams expire readings under the [current freshness policy](polling.md#reading-expiration).
Access loss or receiver failure wakes cleanup immediately rather than waiting
up to the 300-second HA interval.
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

The decoder leaves absent or invalid readings unknown; entities additionally
require an eligible baseline and current freshness. Null/omitted updates preserve
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
Metadata received in split identification updates or authentication replies is
published before deciding whether to enable battery sampling.

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
outside this BLE implementation; transport investigation is tracked in
[#97](https://github.com/sharwell/ha-chandler-legacy-view/issues/97).

## Protocol limitations

The reference's chunks have no sequence number, message ID or payload length.
First/last markers, CRC, JSON validation and request ownership detect malformed
deliveries; a valid first marker restarts assembly, as in Legacy View. These
checks cannot prove that an otherwise valid JSON value was never duplicated
or delayed by the valve. Do not invent sequence fields or claim stronger replay detection than the wire protocol provides.
Hardware reports remain necessary for actual notification ordering, connection
release, proxy slot reuse and firmware-specific behavior.

Historical corrections and recorded validation are preserved in
[the #60 implementation record](https://github.com/sharwell/ha-chandler-legacy-view/issues/60#issuecomment-6960679077).
