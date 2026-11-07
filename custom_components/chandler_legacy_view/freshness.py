"""Reading confirmation and expiration, independent of Bluetooth presence."""

from __future__ import annotations

from dataclasses import dataclass
import logging

from homeassistant.helpers.event import async_call_later
from homeassistant.core import callback

from .const import CONNECTION_ATTEMPT_BUDGET_SECONDS, CONNECTION_MIN_RETRY_INTERVAL

_LOGGER = logging.getLogger(__name__)
RECOVERY_ALLOWANCE = max(
    300, 2 * CONNECTION_ATTEMPT_BUDGET_SECONDS
    + CONNECTION_MIN_RETRY_INTERVAL.total_seconds() + 15,
)
READINGS = frozenset({
    "present_flow", "water_usage_today", "peak_flow_today", "soft_water_remaining",
    "days_until_regeneration", "water_hardness", "battery", "time_of_day",
})


@dataclass(frozen=True)
class FreshnessPolicy:
    mode: str
    interval: float
    maximum_age: float


class ReadingFreshness:
    """Keep absolute deadlines and sticky expiry until a new confirmation."""

    def __init__(self, hass, address, policy, changed):
        self.hass = hass
        self.address = address
        self.policy = policy
        self.changed = changed
        self.started: float | None = None
        self.confirmed: dict[str, float] = {}
        self.expired: set[str] = set()
        self._expired_confirmation: dict[str, float | None] = {}
        self.failures = 0
        self._cancel = None
        self._closed = False

    def activate(self) -> None:
        if self.started is None and not self._closed:
            self.started = self.hass.loop.time()
            self.recalculate()

    def current(self, key) -> bool:
        return self.policy() is None or key not in self.expired

    def close(self) -> None:
        self._closed = True
        if self._cancel is not None:
            self._cancel()
            self._cancel = None

    def _expire(self, keys, reason, policy) -> bool:
        added = set(keys) - self.expired
        self.expired.update(added)
        for key in sorted(added):
            confirmed = self.confirmed.get(key)
            self._expired_confirmation[key] = confirmed
            last = max(self.confirmed.values(), default=None)
            deadline = (
                confirmed + policy.maximum_age if confirmed is not None else
                last + policy.maximum_age if last is not None else
                self.started + RECOVERY_ALLOWANCE if self.started is not None else None
            )
            _LOGGER.debug(
                "Valve %s reading %s expired: %s (mode=%s interval=%.1fs "
                "maximum_age=%.1fs deadline=%s)",
                self.address, key, reason, policy.mode, policy.interval,
                policy.maximum_age, deadline,
            )
        return bool(added)

    def invalidate(self, keys, reason: str) -> None:
        """End session authority immediately, retaining the data for diagnostics."""
        if self._closed or (policy := self.policy()) is None:
            return
        if self._expire(keys, reason, policy):
            self.changed()

    def renew_session(self, keys, received: float) -> None:
        """Renew a known baseline from live traffic without publishing recovery.

        Expired values remain unavailable until the consumer can publish the
        updated snapshot and availability together.
        """
        if self._closed:
            return
        changed = False
        for key in keys:
            if received > self.confirmed.get(key, float("-inf")):
                self.confirmed[key] = received
                changed = True
        if changed:
            self.failures = 0
            self.recalculate(notify=False)

    def confirm(self, confirmations: dict[str, float]) -> None:
        """Prepare availability before the caller publishes the new values."""
        if self._closed or not confirmations:
            return
        self.activate()
        policy = self.policy()
        now = self.hass.loop.time()
        for key, received in confirmations.items():
            previous = self.confirmed.get(key)
            if previous is not None and received < previous:
                continue
            if received == previous and key not in self.expired:
                continue
            self.confirmed[key] = received
            if policy is None or received + policy.maximum_age > now:
                self.failures = 0
            if policy is not None and received + policy.maximum_age <= now:
                self._expire([key], "buffered confirmation already expired", policy)
            elif key in self.expired:
                expired_confirmation = self._expired_confirmation.get(key)
                if expired_confirmation is None or received > expired_confirmation:
                    self.expired.remove(key)
                    _LOGGER.debug("Valve %s reading %s recovered with a new confirmation", self.address, key)
        self.recalculate(notify=False)

    def failed_attempt(self, *, due: bool) -> None:
        """Only a due attempt and its retry justify early expiration."""
        if self._closed or not due or (policy := self.policy()) is None:
            return
        self.activate()
        self.failures += 1
        if self.failures >= 2 and self._expire(READINGS, "due read and retry failed", policy):
            self.changed()

    def recalculate(self, *, notify=True) -> None:
        if self._cancel is not None:
            self._cancel()
            self._cancel = None
        if self._closed or self.started is None or (policy := self.policy()) is None:
            return
        now = self.hass.loop.time()
        last_confirmation = max(self.confirmed.values(), default=None)
        deadlines = {}
        for key in READINGS - self.expired:
            if key in self.confirmed:
                deadlines[key] = self.confirmed[key] + policy.maximum_age
            elif last_confirmation is None:
                deadlines[key] = self.started + RECOVERY_ALLOWANCE
            else:
                # Missing fields in an otherwise healthy exchange stay unknown.
                deadlines[key] = last_confirmation + policy.maximum_age
        elapsed = [key for key, deadline in deadlines.items() if deadline <= now]
        changed = self._expire(elapsed, "confirmation deadline elapsed", policy)
        remaining = [deadline for key, deadline in deadlines.items() if key not in self.expired]
        if remaining:
            self._cancel = async_call_later(self.hass, max(0.001, min(remaining) - now), self._handle_deadline)
        if changed and notify:
            self.changed()

    @callback
    def _handle_deadline(self, _):
        self._cancel = None
        self.recalculate()
