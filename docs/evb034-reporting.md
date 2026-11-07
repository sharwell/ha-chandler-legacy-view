# Evb034 reporting evidence for reading expiration

This is the per-field decision record for #105. It separates the implemented,
user-selected session-health policy from the firmware reporting guarantees
that remain unknown. Simulation does not establish a firmware guarantee.

## Selected implementation policy

On 2026-11-07 the user selected a session-based approach: a valid received
field establishes eligibility, and later updates in the same session renew
all eligible fields. This deliberately replaces the original proposal's
requirement to establish a separate reporting contract before retaining each
field. It is an integration policy assumption, not an APK-proven guarantee.

Eligibility affects availability, not entity creation or registry enablement.
The implementation uses these rules:

- Establish a baseline only from explicit valid authenticated reading values.
  Session initialization must finish before the baseline can be published as
  available. Zero is valid. Fields can become eligible later; they need not
  appear in the first packet.
- Require every component of a composite value. Clock eligibility needs hour
  and minute, plus AM/PM on non-Clack C6.00–6.08.
- Any complete validated authenticated packet with a usable known reading
  component renews every eligible baseline in that session. Null-only, invalid,
  metadata-only, ACK and keepalive traffic does not renew it. Invalid/null
  fields cannot erase a previously valid baseline.
- Observe receipt independently of HA publication. Keep original monotonic
  receipt times through buffering. Publish first availability and recovery
  with the current snapshot so an old value is never briefly resurrected.
- Never make an unseen field available merely because another field updates.
  Reset eligibility for a new connection; access/receiver failure or detected
  unexpected disconnect invalidates the old baseline. Late old callbacks
  cannot establish eligibility in a replacement session.
- A healthy on-demand read retains its completed snapshot between connections,
  subject to the on-demand expiry deadline. Deliberate disconnect is not an
  immediate snapshot expiry. Stopping a healthy persistent session first
  publishes any already-received buffered readings with their original times.
- Keep existing firmware/type/Clack availability restrictions even when a
  value has been received. Increasing the interval cannot revive expired data.

Persistent silence expires eligible baselines after `max(3 * P, 120)` seconds.
On-demand snapshots expire after `I + max(300, 2 * A + C + 15)` seconds, using
the existing bounded attempt and cooldown budgets. A due read and its failed
retry can cause earlier expiry. Recovery continues to respect ownership,
cooldown and authentication lockout.

**Limitation:** one field's producer can stop updating while other reading
traffic keeps its retained value marked fresh. This policy detects loss of
session reading activity, not every individual producer failure. Clock and
other changing fields have the same limitation once their baseline is known.

## Reference and method findings

Reference: Water System / Legacy View 6.18.2, extracted from the user's phone.
Base APK SHA-256:
`f6a78638b1bfd943f9897a2eda8bb2001703fa671ef565b8547ba65ccfa0f4bc`.
The ignored bytecode root is
`local-artifacts/chandler-legacy-view/6.18.2/bytecode/ir/smali/base/classes/com/chandlersystemsinc/`.
Line numbers below refer to this disassembly, not decompiled source.

| Smali method | Evidence and limit |
| --- | --- |
| `signaturebluetooth/viewmodels/CsBleViewModel.onBleProcessorPacketReceived(Map)` (line 2213) | Passes the change map into `CsBlePacket$Companion.updatePacket`, stores the merged packet and publishes it to LiveData. No field-age or retransmission contract is established here. |
| `signaturebluetooth/models/evb034/CsBlePacket$Companion.updatePacket(CsBlePacket, Map)` (line 1671; null check at 1805) | Updates the existing packet from supplied keys and skips JSON nulls. Omitted fields retain their values. This establishes delta merging, not periodic reporting or guaranteed delivery of every relevant change. |
| `signaturebluetooth/viewmodels/CsBleViewModel.onBleDeviceFullConnected(String)` (line 1998) | Starts the queue processor and sends the Evb034 identification packet for firmware 600+. It does not establish a Dashboard polling interval. |
| `signaturebluetooth/core/queue/CsBleQueueProcessor.processStatusPacket(CsBleManager, byte[])` (line 1355) | Handles keepalive, ACK/NAK and timeout status. Keepalive status has no individual reading values or producer-health declaration. |
| `CsBleQueueProcessor.writeKeepAlivePacket(...)` (line 2031) and `restartTimeout()` (line 1669) | Emit transport status and manage processor timeout. The three-second processor deadline concerns wire exchanges, not a field's maximum unchanged reporting gap. |
| `signaturebluetooth/core/CsDataBufferEvb034$Companion.setBatteryReadEnabled(boolean)` (line 3704) | Enables battery reading; it does not state how often `dbl` must be reported afterward. |

These findings do not establish a maximum per-field report interval or a
change-only subscription health guarantee. No firmware source, manufacturer
reporting specification or timestamped C6.x hardware captures are available
in this workspace. The fake peer's one-second cadence is a test scenario.

A public documentation search on 2026-11-07 also did not yield a usable
per-field guarantee. Manufacturer manual search results describe app setup
and Dashboard controls; direct manual fetches failed. The
[EVB-034 user-manual filing](https://fccid.io/SWP-EVB-034/User-Manual/Users-Manual-7376812)
was located, but its document could not be retrieved (HTTP 403). These search
and access limits are not evidence that a reporting contract cannot exist.

## Per-field and firmware decision record

**U** means unclassified: periodic, change-only and initial-only behavior have
not been distinguished. For every U cell the guaranteed maximum report/health
interval is unknown. The selected implementation renews eligible fields from
session reading activity as described above, rather than claiming that U
means periodic or that unrelated traffic proves each producer is healthy.
Applicable Clack variants need their own evidence; none is inferred from a
non-Clack capture or from a conversion boundary.

| Field | Existing HA reading | Explicit baseline/confirmation dependencies | C6.00–6.08 | C6.09–6.10 | C6.11+ | Clack |
| --- | --- | --- | --- | --- | --- | --- |
| `gpf` | Present Flow | Valid explicit numeric `gpf`; unchanged zero is a confirmation | U | U | U | U |
| `dwu` | Water Usage Today | Valid explicit `dwu`; version/Clack conversion affects value, not report cadence | U | U | U | U |
| `dpfd` | Peak Flow Today | Valid explicit numeric `dpfd` | U | U | U | U |
| `dtgr` | Soft Water Remaining | Valid explicit `dtgr`, applicable metered-valve gate and conversion | U | U | U | U |
| `dcdo` | Days Until Regeneration/Backwash | Valid explicit `dcdo` and applicable non-metered gate | U | U | U | U |
| `dwh` | Water Hardness | Valid explicit `dwh` and applicable metered-valve gate | U | U | U | U |
| `dbl` | Battery | Valid explicit `dbl`; battery reading enabled; existing Clack exclusion still applies | U | U | U | Excluded by APK UI; producer contract unclassified |
| `dh/dm/da` | Time of Day | Valid hour/minute; C6.00–6.08 also needs AM/PM. C6.09+ and Clack interpret hour as 24-hour time. Components need coherent baseline and per-component receipt times. | U | U | U | U |

An explicit valid field can establish its baseline. Missing/null/invalid fields,
advertisements, ACKs, keepalive status and unrelated metadata do not prove that
its producer is current. A verified change-only contract would additionally
require an authenticated baseline, bounded application-health evidence for
the relevant producer and resynchronization after any loss. Reconnect, access
loss, receiver failure or unrecoverable packet/queue loss cannot preserve that
old baseline's authority.

## Evidence needed to finish classification

For each applicable firmware family and Clack variant, record model, firmware,
type/twin/Clack flags, adapter/proxy, HA/ESPHome and app versions. Capture
timestamped complete validated JSON before coalescing, plus access/session
transitions and status traffic. Pair Legacy View observations where possible.

Observe unchanged readings beyond the candidate expiry windows, including
zero flow and five-minute HA publication intervals. Make safe independent
changes where practical; inspect thresholds and clock component boundaries.
Distinguish largest observed gaps from a guaranteed maximum. A finite capture
without repetition cannot prove a change-only contract.

Exercise selective omissions, total silence, metadata/ACK-only traffic,
fragmentation/retransmission, packet/queue loss, disconnect/reconnect and
authentication loss. Establish which application-health indication, if any,
guarantees continued reporting of each retained field. Repeat across families
without extrapolating one valve's result to all versions.

Automated regression coverage exercises the selected session-health policy
across the conversion families and Clack variants. These are simulated peer
scenarios, not hardware evidence classifying the actual firmware. Producer
classification and physical adapter/proxy recovery remain validation gaps;
the implementation must not be described as a verified per-field health
guarantee. These gaps are recorded for #106 without claiming physical results.
