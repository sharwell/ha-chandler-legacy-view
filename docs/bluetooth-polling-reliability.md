# Bluetooth polling reliability

Implement locked story #106 on `bluetooth-polling-reliability`, using the
issue bodies and comments read at the beginning of this work. The initial
working tree and index were clean at `9998c2e`. The ignored snapshot is
`local-artifacts/bluetooth-polling-reliability/locked-issues.json`.

## Objective and sequence

Each numbered issue is one validated commit. Its behavior and failure tests
belong in that commit. Existing protocol mappings, entity identity, support
gates and authenticated Evb034 notification processing remain authoritative.

1. **#102: stalled operations.** Bound transport calls and finite exchanges;
   retain expired work until safe release, reject late notifications/results,
   and recover without overlapping clients. Validate through the shared
   Bluetooth boundary, including cancellation-resistant work and independent
   valves. Healthy persistent sessions have no lifetime limit.
2. **#103: lifecycle.** Use owned operation cleanup to bound switch-off,
   unload/reload and shutdown. Preserve repeated cancellation and concurrent
   callers. An unconfirmed release needs a truthful failed/quarantined
   outcome, including across a replacement entry. Validate real HA lifecycle
   states and distinguishable old/new readings.
3. **#104: durable preferences and compatibility.** Store persistent mode,
   an integration-default persistent interval (10 seconds, range 5–300),
   and optional valve overrides. Retain the existing number as a working
   compatibility interface: writes always create explicit overrides, even
   when equal to the inherited default. Verify options, local writes while
   radio-unavailable, reload and storage-backed restoration independently of
   entity enablement. Deprecation/removal belongs to separate follow-up #107.
4. **#100: scheduling and standard refresh.** Keep a 15-minute on-demand
   default, with integration options and optional valve overrides. All
   automatic triggers obey successful-read eligibility. Implement
   `homeassistant.update_entity`, coalesced per valve, without new control
   entities. Document custom polling automations, refresh scripts, their
   Related links, and suspension of automatic on-demand polling. Validate
   actual HA actions, deadlines, independent valves and restored settings.
5. **#105: freshness.** Use monotonic confirmation times, not publication,
   attempts or advertisement times. On-demand expiry is the effective
   interval plus `max(300, 2 * attempt_budget + cooldown + 15)` seconds.
   Persistent periodic readings expire after `max(3 * interval, 120)`.
   A due read and permitted retry that both fail can expire data earlier;
   an early manual failure alone does not invalidate still-current data.
   Preserve receipt timestamps through Evb034 coalescing and distinguish
   periodic versus verified change-only fields. Validate published expiry
   and recovery, startup, null/partial traffic and interval changes.

The final result must preserve one owned interactive client per valve,
restore user choices before starting work, leave the link free between
on-demand reads, and represent expired data honestly. #106 can be closed
only when all its required outcomes and acceptance evidence are satisfied.

## Validation and completion boundaries

Use the existing real HA fixtures, shared simulated Bluetooth boundary and
controlled clock. Run the applicable checks on the declared minimum/current
HA environments, and the complete suites before each commit. There is no
configured formatter in this repository. Review the full staged diff and
verify each stored commit message. Do not push or edit the locked issues.

Simulated disconnect/cleanup evidence does not establish physical valve or
ESPHome proxy-slot release. Record available live evidence and missing
firmware/adapter/proxy coverage explicitly.

The original #105 comment requires a per-field/firmware decision record for Evb034:
the APK merges partial maps and skips nulls, but that alone does not prove a
maximum report interval or a change-only health contract. The user has no
Evb034 hardware. Firmware classification cannot be inferred from the fake's
one-second stream. Resolve the reference/documentation evidence and record
remaining capture requirements before claiming #105 or #106 complete.

On 2026-11-07 the user selected the session-health policy recorded below as
the implementation approach despite the unresolved firmware classifications.
That explicit direction replaces the original proof-before-retention gate for
#105 implementation; it does not supply missing hardware/producer evidence.

## Unexpected situations and open questions

The bounded operation wrapper adds scheduling turns before the persistent
handoff. Three existing tests requested stop before polling and depended on
that poll reaching the handoff without yielding. They now request stop at
the actual handoff; their ownership and cancellation assertions are retained.

HA's `FAILED_UNLOAD` state is terminal, even if a backend later releases its
connection. #103 therefore uses the planned quarantined outcome: logical
unload removes entities/listeners, logs the cleanup failure and retains
external ownership separately. Replacement setup raises `ConfigEntryNotReady`
until release, allowing HA's normal setup retry rather than requiring a
restart. Platform refusal/cancellation retains the existing HA semantics.

A failed assertion with an intentionally cancellation-resistant fake call
left the test loop's final task drain waiting on its artificial gate. The
shared transport fixture now releases those gates during test teardown.

HA's default forced entity update catches failures and logs them while its
action returns normally. Dashboard sensors therefore perform the explicitly
requested refresh before publishing state, allowing its action error to reach
the caller. Automatic update behavior and other platforms remain unchanged.

The #105 APK review confirms the known Evb034 evidence gap. Delta merging and
transport keepalive methods do not specify per-field reporting/health limits.
The user's subsequent direction resolves the implementation boundary using
session-based eligibility and renewal. `docs/evb034-reporting.md` records the
selected assumption, every field/family as unclassified and required captures.
One stopped producer can remain undetected while other reading traffic arrives.

## Operation budgets and evidence (#102)

Connection establishment has 20 seconds. Individual GATT calls have 10
seconds, while the entire initial exchange has 60 seconds (including the
existing four-attempt authentication sequence). Persistent Evb019 exchanges
have 30 seconds; healthy persistent session lifetime remains unlimited.
The existing Evb034 three-second wire deadlines and acknowledgements remain
unchanged. Request unsubscribe has a five-second unwind allowance; reset or
stream closure has a separate five seconds and disconnect has 30 seconds.
Thus a complete initial attempt is bounded by 120 seconds. Timed-out external
work remains owned even after these waits end; a deadline is not evidence
that its transport released the connection.

Minimum/current HA suites exercise cooperative and cancellation-resistant
connect, discovery, subscription, authentication and reading operations,
late callbacks/results, independent valves, and authenticated Evb034 streams
through simulated Bluetooth I/O. Live adapter/proxy release, shared physical
capacity and phone-app handoff remain unverified.

## Lifecycle budgets and evidence (#103)

Switch-off and unload invalidate callbacks and cancel/join the same owned
poll/session. Cleanup retains the independent five-second unsubscribe,
five-second reset/stream-close and thirty-second disconnect allowances, with
a 45-second total caller wait. Repeated cancellation preserves that deadline
and the caller's cancellation result. Unknown release is quarantined across
entry runtimes; switch-off reports an action error while retaining "off".

Real HA lifecycle tests with simulated GATT cover cooperative reload and
fresh published values, resistant late connect/read/disconnect, retryable
replacement setup, another entry, unsubscribe failure, repeated cancellation
and queued/running handoffs. No physical adapter/proxy evidence is available.

## Durable settings and evidence (#104)

Entry options own the persistent default and address-specific mode/interval
preferences. Constructors read them before scheduling any connection; entity
enablement is irrelevant to restoration. Preference edits update live controls
and reschedule publication without reloading valves or restarting a healthy
stream. Credential changes retain their existing reload behavior.

The existing interval number keeps its registry identity and enablement.
Every valid write saves an explicit override, even if equal to the inherited
default, and it remains available as local configuration when the radio is
unavailable. Options can inspect, edit and clear overrides. No deprecation
notice, entity removal or new entity is introduced.

Migration runs once before entity creation. Valid non-default historical HA
states can recover an old selection; authoritative options win. Old off/10
defaults and unavailable/unknown states do not prove a saved selection. Old
releases did not persist these controls, so previously lost choices cannot
be reconstructed. Existing startup number actions now capture durable values.

Real HA actions, options, registries and storage-backed fresh runtimes verify
inheritance, explicit writes, offline edits, migration, invalid values, saved
on/off, disabled controls and restored publication cadence. Bluetooth I/O is
simulated; storage and HA lifecycle are real.

## Polling and refresh evidence (#100)

Automatic advertisement, periodic, due-time and deferred triggers recheck the
same successful-reading deadline. The default remains 15 minutes; options use
1–1440 minutes with entry defaults and valve overrides. A dedicated deadline
timer handles intervals shorter than the manager's periodic check and avoids
depending on a future advertisement. Failed reads retain cooldown-governed
recovery; locked-out credentials do not open more connections.

Real `homeassistant.update_entity` calls join shared valve work, wait through
cooldown, use existing persistent sessions and report unsuccessful reads.
Evb034 field receipt timestamps survive packet coalescing and publication.
HA's `pref_disable_polling` governs every automatic on-demand trigger; manual
refresh and explicitly enabled persistent sessions remain usable.

Regression evidence includes simulated C4.40 advertisement cadence, due times
without advertisements, independent valve intervals, defaults/overrides,
disabled automatic polling, cancellation/unload, multi-target refresh and
Evb019/Evb034 persistent behavior. Fresh HA storage restoration includes the
on-demand default and override. The documented automation/script examples run
through HA and appear in its entity relationship lookup. Live C4.40/proxy
connection-frequency, slot-release and phone-app handoff remain unverified.

## Reading expiration and evidence (#105)

The Evb019 portion implements monotonic confirmation, independent expiration
timers, due-attempt/retry early failure, unknown initial acquisition, live
interval/mode recalculation and sticky expiration until fresh data. Recovery
publishes the fresh value and availability together. Early manual failures
retain current data and do not create extra automatic retries before due time.
Existing Bluetooth and APK capability availability conditions remain in force.

Evb034 implements the selected session policy. A complete valid baseline makes
an applicable field eligible; later valid reading activity renews all eligible
fields in that authenticated connection. Unseen fields stay unavailable.
Metadata/ACK/null/invalid traffic cannot renew them. Receipt is observed before
publication, while first availability and recovery publish a fresh snapshot
atomically. New connections and access/receiver loss invalidate eligibility.
Healthy on-demand snapshots retain their bounded between-read lifetime.

Regression checks use real HA states/events and simulated Bluetooth I/O. They
cover both expiry formulas, startup, due failures/retries, early manual failures,
coalescing timestamps, long publication intervals, mode/interval changes,
unchanged zero, independent valves, supported/unsupported entities, late old
callbacks, new baselines and clock dependencies across conversion families and
Clack variants. Session initialization still must finish before first success.

The final minimum/current HA suites each pass 536 tests and 294 subtests.
Initialization-failure regressions exposed a need to defer baseline publication
until the required battery-read command is acknowledged. The receiver can
retain data during initialization, but cannot report a successful read early.

All five software steps and their failure tests are complete under the user's
revised #105 policy, completing #106's implementation outcomes. The #105 commit
therefore includes the requested #106 closing line. Actual Evb034 producer
classification and physical adapter/proxy release remain unverified; these
are recorded evidence gaps, not simulated physical results. Linux CI was not
run locally and remains for the PR checks alongside the Windows targets.
