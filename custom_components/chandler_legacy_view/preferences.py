"""Authoritative entry options for local valve monitoring preferences."""

from __future__ import annotations

import math

from homeassistant.helpers import entity_registry as er, restore_state

from .const import (
    DEFAULT_PERSISTENT_POLL_INTERVAL_SECONDS, DOMAIN,
    MAX_PERSISTENT_POLL_INTERVAL_SECONDS, MIN_PERSISTENT_POLL_INTERVAL_SECONDS,
)

PERSISTENT_INTERVAL = "persistent_poll_interval"
PERSISTENT_ENABLED = "persistent_connection"
VALVE_PREFERENCES = "valve_preferences"
PREFERENCES_VERSION = "preferences_version"
INHERIT_PERSISTENT_INTERVAL = "inherit_persistent_interval"
ON_DEMAND_INTERVAL = "on_demand_poll_interval"
INHERIT_ON_DEMAND_INTERVAL = "inherit_on_demand_interval"


def on_demand_interval(value) -> float:
    """On-demand options use minutes; never confuse them with persistent seconds."""
    if isinstance(value, bool):
        raise ValueError("On-demand interval must be a number")
    value = float(value)
    if not math.isfinite(value) or not 1 <= value <= 1440:
        raise ValueError("On-demand interval must be between 1 and 1440 minutes")
    return value


def default_on_demand_interval(options) -> float:
    try:
        return on_demand_interval(options.get(ON_DEMAND_INTERVAL, 15))
    except (ValueError, TypeError, OverflowError):
        return 15.0


def persistent_interval(value) -> float:
    """Reject malformed values instead of silently changing user intent."""
    if isinstance(value, bool):
        raise ValueError("Persistent interval must be a number")
    value = float(value)
    if not math.isfinite(value) or not MIN_PERSISTENT_POLL_INTERVAL_SECONDS <= value <= MAX_PERSISTENT_POLL_INTERVAL_SECONDS:
        raise ValueError("Persistent interval must be between 5 and 300 seconds")
    return value


def default_interval(options) -> float:
    try:
        return persistent_interval(options.get(PERSISTENT_INTERVAL, DEFAULT_PERSISTENT_POLL_INTERVAL_SECONDS))
    except (ValueError, TypeError, OverflowError):
        return DEFAULT_PERSISTENT_POLL_INTERVAL_SECONDS


def valve_preferences(options, address) -> dict:
    values = options.get(VALVE_PREFERENCES, {})
    value = values.get(address, {}) if isinstance(values, dict) else {}
    return dict(value) if isinstance(value, dict) else {}


class ValvePreferences:
    """Read current options and merge every write into the latest entry data."""

    def __init__(self, hass, entry):
        self.hass = hass
        self.entry = entry

    def enabled(self, address) -> bool:
        return valve_preferences(self.entry.options, address).get(PERSISTENT_ENABLED) is True

    def interval(self, address) -> float:
        try:
            return persistent_interval(valve_preferences(self.entry.options, address)[PERSISTENT_INTERVAL])
        except (KeyError, ValueError, TypeError, OverflowError):
            return default_interval(self.entry.options)

    def set(self, address, key, value) -> None:
        options = dict(self.entry.options)
        original = options.get(VALVE_PREFERENCES, {})
        valves = dict(original) if isinstance(original, dict) else {}
        values = valve_preferences(options, address)
        values[key] = value
        valves[address] = values
        options[VALVE_PREFERENCES] = valves
        self.hass.config_entries.async_update_entry(self.entry, options=options)

    def on_demand_interval(self, address) -> float:
        """Return the effective expected interval in seconds for scheduling."""
        try:
            minutes = on_demand_interval(valve_preferences(self.entry.options, address)[ON_DEMAND_INTERVAL])
        except (KeyError, ValueError, TypeError, OverflowError):
            minutes = default_on_demand_interval(self.entry.options)
        return minutes * 60

    def migrate(self) -> None:
        """Import recoverable pre-upgrade evidence once, before creating objects.

        Old releases did not save preferences. An initialized off/10 state
        cannot prove a user choice; only valid non-default historical values
        can be recovered. Existing authoritative configuration always wins.
        """
        if self.entry.options.get(PREFERENCES_VERSION) == 1:
            return
        options = dict(self.entry.options)
        original = options.get(VALVE_PREFERENCES, {})
        valves = dict(original) if isinstance(original, dict) else {}
        restore_data = restore_state.async_get(self.hass)
        get_stored_state = getattr(restore_data, "async_get_stored_state", None)
        if get_stored_state is None:
            # HA versions before 2026.11 expose only the entity-ID mapping.
            get_stored_state = restore_data.last_states.get
        for record in er.async_entries_for_config_entry(er.async_get(self.hass), self.entry.entry_id):
            if record.platform != DOMAIN:
                continue
            key = next((key for key in (PERSISTENT_ENABLED, PERSISTENT_INTERVAL)
                        if record.unique_id.endswith("_" + key)), None)
            if key is None:
                continue
            address = record.unique_id[:-(len(key) + 1)]
            values = valve_preferences({VALVE_PREFERENCES: valves}, address)
            if key in values or (key == PERSISTENT_INTERVAL and PERSISTENT_INTERVAL in options):
                continue
            state = self.hass.states.get(record.entity_id)
            if state is None:
                stored = get_stored_state(record.entity_id)
                if stored is not None:
                    state = stored.state
            if state is None:
                continue
            if key == PERSISTENT_ENABLED:
                if state.state != "on":
                    continue
                value = True
            else:
                try:
                    value = persistent_interval(state.state)
                except (ValueError, TypeError, OverflowError):
                    continue
                if value == DEFAULT_PERSISTENT_POLL_INTERVAL_SECONDS:
                    continue
            values[key] = value
            valves[address] = values
        if valves:
            options[VALVE_PREFERENCES] = valves
        options[PREFERENCES_VERSION] = 1
        self.hass.config_entries.async_update_entry(self.entry, options=options)
