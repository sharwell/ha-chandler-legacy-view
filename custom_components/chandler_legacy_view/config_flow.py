"""Config flow for the Chandler Legacy View integration."""

from __future__ import annotations

import re

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_DEFAULT_PASSCODE,
    CONF_DEVICE_ADDRESS,
    CONF_DEVICE_PASSCODE,
    CONF_DEVICE_PASSCODES,
    CONF_REMOVE_OVERRIDE,
    DATA_DISCOVERY_MANAGER,
    DEFAULT_VALVE_PASSCODE,
    DOMAIN,
)
from .entity import friendly_name_from_advertised_name
from .preferences import (
    PERSISTENT_INTERVAL, VALVE_PREFERENCES, INHERIT_PERSISTENT_INTERVAL,
    default_interval, persistent_interval, valve_preferences,
    ON_DEMAND_INTERVAL, INHERIT_ON_DEMAND_INTERVAL,
    default_on_demand_interval, on_demand_interval,
)


PASSCODE_PATTERN = re.compile(r"^\d{4}$")

PASSCODE_SELECTOR = TextSelector(
    TextSelectorConfig(
        type=TextSelectorType.PASSWORD,
    )
)


def _coerce_passcode(value: object | None) -> str | None:
    """Return a normalized passcode string or ``None`` if not provided."""

    if value is None:
        return None

    if not isinstance(value, str):
        value = str(value)

    normalized = value.strip()
    if not normalized:
        return None

    return normalized


def _is_valid_passcode(value: str) -> bool:
    """Return ``True`` if the provided value is a four-digit passcode."""

    return bool(PASSCODE_PATTERN.fullmatch(value))


class ChandlerLegacyViewConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Chandler Legacy View."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, object] | None = None
    ) -> FlowResult:
        """Handle the initial step initiated by the user."""

        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        return self.async_create_entry(
            title="Chandler Legacy View",
            data={CONF_DEFAULT_PASSCODE: DEFAULT_VALVE_PASSCODE},
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> ChandlerLegacyViewOptionsFlowHandler:
        """Create the options flow handler for Chandler Legacy View."""

        return ChandlerLegacyViewOptionsFlowHandler(config_entry)


class ChandlerLegacyViewOptionsFlowHandler(config_entries.OptionsFlow):
    """Allow updates to passcode configuration for Chandler valves."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize the options flow handler."""

        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, object] | None = None
    ) -> FlowResult:
        """Handle the default options step."""

        errors: dict[str, str] = {}
        hass = self.hass
        existing_overrides = dict(
            self._config_entry.options.get(CONF_DEVICE_PASSCODES, {})
        )
        updated_overrides = dict(existing_overrides)
        overrides_changed = False

        discovery_manager = (
            hass.data.get(DOMAIN, {})
            .get(self._config_entry.entry_id, {})
            .get(DATA_DISCOVERY_MANAGER)
        )

        device_options: list[dict[str, str]] = []
        seen_addresses: set[str] = set()

        if discovery_manager is not None:
            for address, advertisement in sorted(discovery_manager.devices.items()):
                label = friendly_name_from_advertised_name(advertisement.name)
                device_options.append(
                    {
                        "value": address,
                        "label": f"{label} ({address})",
                    }
                )
                seen_addresses.add(address)

        saved_valves = self._config_entry.options.get(VALVE_PREFERENCES, {})
        if not isinstance(saved_valves, dict):
            saved_valves = {}
        for address in sorted(set(existing_overrides) | set(saved_valves)):
            if address in seen_addresses:
                continue
            device_options.append({"value": address, "label": address})

        if user_input is not None:
            updated_options = dict(self._config_entry.options)
            if PERSISTENT_INTERVAL in user_input:
                try:
                    updated_options[PERSISTENT_INTERVAL] = persistent_interval(user_input[PERSISTENT_INTERVAL])
                except (ValueError, TypeError, OverflowError):
                    errors[PERSISTENT_INTERVAL] = "invalid_interval"
            if ON_DEMAND_INTERVAL in user_input:
                try:
                    updated_options[ON_DEMAND_INTERVAL] = on_demand_interval(user_input[ON_DEMAND_INTERVAL])
                except (ValueError, TypeError, OverflowError):
                    errors[ON_DEMAND_INTERVAL] = "invalid_on_demand_interval"

            default_passcode_input = _coerce_passcode(
                user_input.get(CONF_DEFAULT_PASSCODE)
            )
            if default_passcode_input is not None:
                if not _is_valid_passcode(default_passcode_input):
                    errors[CONF_DEFAULT_PASSCODE] = "invalid_passcode"

            remove_override = bool(user_input.get(CONF_REMOVE_OVERRIDE))
            selected_device = user_input.get(CONF_DEVICE_ADDRESS)
            device_passcode_input = _coerce_passcode(
                user_input.get(CONF_DEVICE_PASSCODE)
            )

            interval_input = user_input.get("device_persistent_interval")
            inherit_interval = bool(user_input.get(INHERIT_PERSISTENT_INTERVAL))
            on_demand_input = user_input.get("device_on_demand_interval")
            inherit_on_demand = bool(user_input.get(INHERIT_ON_DEMAND_INTERVAL))
            if selected_device is None:
                if interval_input is not None or inherit_interval or on_demand_input is not None or inherit_on_demand:
                    errors["base"] = "device_required"
                if device_passcode_input is not None or remove_override:
                    errors["base"] = "device_required"
            else:
                if remove_override:
                    if device_passcode_input is not None:
                        errors[CONF_DEVICE_PASSCODE] = "passcode_not_expected"
                    else:
                        overrides_changed = existing_overrides.get(selected_device) is not None
                        updated_overrides.pop(selected_device, None)
                elif device_passcode_input is not None:
                    if not _is_valid_passcode(device_passcode_input):
                        errors[CONF_DEVICE_PASSCODE] = "invalid_passcode"
                    else:
                        overrides_changed = (
                            existing_overrides.get(selected_device) != device_passcode_input
                        )
                        updated_overrides[selected_device] = device_passcode_input
                elif interval_input is None and not inherit_interval and on_demand_input is None and not inherit_on_demand:
                    errors[CONF_DEVICE_PASSCODE] = "passcode_required"

                values = valve_preferences(updated_options, selected_device)
                if inherit_on_demand:
                    if on_demand_input is not None:
                        errors["device_on_demand_interval"] = "interval_not_expected"
                    else:
                        values.pop(ON_DEMAND_INTERVAL, None)
                elif on_demand_input is not None:
                    try:
                        values[ON_DEMAND_INTERVAL] = on_demand_interval(on_demand_input)
                    except (ValueError, TypeError, OverflowError):
                        errors["device_on_demand_interval"] = "invalid_on_demand_interval"
                if inherit_interval:
                    if interval_input is not None:
                        errors["device_persistent_interval"] = "interval_not_expected"
                    else:
                        values.pop(PERSISTENT_INTERVAL, None)
                elif interval_input is not None:
                    try:
                        values[PERSISTENT_INTERVAL] = persistent_interval(interval_input)
                    except (ValueError, TypeError, OverflowError):
                        errors["device_persistent_interval"] = "invalid_interval"
                if interval_input is not None or inherit_interval or on_demand_input is not None or inherit_on_demand:
                    original = updated_options.get(VALVE_PREFERENCES, {})
                    valves = dict(original) if isinstance(original, dict) else {}
                    valves[selected_device] = values
                    updated_options[VALVE_PREFERENCES] = valves

            if not errors:
                if default_passcode_input is not None:
                    hass.config_entries.async_update_entry(
                        self._config_entry,
                        data={**self._config_entry.data, CONF_DEFAULT_PASSCODE: default_passcode_input},
                    )
                if overrides_changed:
                    if updated_overrides:
                        updated_options[CONF_DEVICE_PASSCODES] = updated_overrides
                    else:
                        updated_options.pop(CONF_DEVICE_PASSCODES, None)
                else:
                    updated_overrides = existing_overrides
                return self.async_create_entry(title="", data=updated_options)

        schema_dict: dict[vol.Marker, object] = {
            vol.Optional(ON_DEMAND_INTERVAL, default=default_on_demand_interval(self._config_entry.options)): NumberSelector(
                NumberSelectorConfig(min=1, max=1440, step=1, unit_of_measurement="min", mode=NumberSelectorMode.BOX)
            ),
            vol.Optional(PERSISTENT_INTERVAL, default=default_interval(self._config_entry.options)): NumberSelector(
                NumberSelectorConfig(min=5, max=300, step=1, unit_of_measurement="s", mode=NumberSelectorMode.BOX)
            ),
            vol.Optional(
                CONF_DEFAULT_PASSCODE,
                default=self._config_entry.data.get(
                    CONF_DEFAULT_PASSCODE, DEFAULT_VALVE_PASSCODE
                ),
            ): PASSCODE_SELECTOR,
        }

        if device_options:
            schema_dict[vol.Optional(CONF_DEVICE_ADDRESS)] = SelectSelector(
                SelectSelectorConfig(
                    options=device_options,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
            schema_dict[vol.Optional(CONF_DEVICE_PASSCODE)] = PASSCODE_SELECTOR
            schema_dict[vol.Optional("device_persistent_interval")] = NumberSelector(
                NumberSelectorConfig(min=5, max=300, step=1, unit_of_measurement="s", mode=NumberSelectorMode.BOX)
            )
            schema_dict[vol.Optional(INHERIT_PERSISTENT_INTERVAL, default=False)] = BooleanSelector()
            schema_dict[vol.Optional("device_on_demand_interval")] = NumberSelector(
                NumberSelectorConfig(min=1, max=1440, step=1, unit_of_measurement="min", mode=NumberSelectorMode.BOX)
            )
            schema_dict[vol.Optional(INHERIT_ON_DEMAND_INTERVAL, default=False)] = BooleanSelector()
            schema_dict[vol.Optional(CONF_REMOVE_OVERRIDE, default=False)] = (
                BooleanSelector()
            )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema_dict),
            errors=errors,
            description_placeholders={"persistent_overrides": "; ".join(
                f"{address}: {values[PERSISTENT_INTERVAL]} s"
                for address in sorted(saved_valves)
                for values in [valve_preferences(self._config_entry.options, address)]
                if PERSISTENT_INTERVAL in values
            ) or "None", "on_demand_overrides": "; ".join(
                f"{address}: {values[ON_DEMAND_INTERVAL]} min"
                for address in sorted(saved_valves)
                for values in [valve_preferences(self._config_entry.options, address)]
                if ON_DEMAND_INTERVAL in values
            ) or "None"},
        )
