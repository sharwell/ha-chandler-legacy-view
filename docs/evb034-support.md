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
4. #60: apply Clack/EBX044 metadata, type mappings and conversions, with a
   support record that distinguishes automated coverage from hardware proof.
5. #61: obtain paired app/HA values and live connection/recovery evidence
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
a 2048-byte receive limit. The existing DeviceList and Dashboard request
payloads are framed for C6.x, rather than sent as raw Evb019 writes.

Identification alone is not a successful Dashboard read. Packet assembly and
notification callbacks belong to one connection; cancelled work cannot supply
identification to a replacement connection. Tests cover CRC/JSON rejection,
fragmentation, coalescing, MTU sizes, bounded ACK/NAK/timeouts and cancellation
alongside all Evb019 regressions.

## Protocol limitations and remaining evidence

The reference's chunks have no sequence number, message ID or payload length.
First/last markers, CRC, JSON validation and request ownership detect malformed
or overlapping deliveries, but cannot prove that an otherwise valid JSON
value was never duplicated or delayed by the valve. Do not invent sequence
fields or claim stronger replay detection than the wire protocol provides.
Hardware reports remain necessary for actual notification ordering, connection
release, proxy slot reuse and firmware-specific behavior.
