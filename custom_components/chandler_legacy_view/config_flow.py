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
    DOMAIN,
)
from .entity import friendly_name_from_advertised_name


PASSCODE_PATTERN = re.compile(r"^\d{4}$")

PASSCODE_SELECTOR = TextSelector(
    TextSelectorConfig(
        type=TextSelectorType.PASSWORD,
        min=4,
        max=4,
        pattern=r"\\d*",
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

        errors: dict[str, str] = {}

        if user_input is not None:
            passcode = _coerce_passcode(user_input.get(CONF_DEFAULT_PASSCODE))
            if passcode is None or not _is_valid_passcode(passcode):
                errors[CONF_DEFAULT_PASSCODE] = "invalid_passcode"
            else:
                return self.async_create_entry(
                    title="Chandler Legacy View",
                    data={CONF_DEFAULT_PASSCODE: passcode},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_DEFAULT_PASSCODE): PASSCODE_SELECTOR}),
            errors=errors,
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

        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, object] | None = None
    ) -> FlowResult:
        """Handle the default options step."""

        errors: dict[str, str] = {}
        hass = self.hass
        existing_overrides = dict(
            self.config_entry.options.get(CONF_DEVICE_PASSCODES, {})
        )
        updated_overrides = dict(existing_overrides)
        overrides_changed = False

        discovery_manager = (
            hass.data.get(DOMAIN, {})
            .get(self.config_entry.entry_id, {})
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

        for address in existing_overrides:
            if address in seen_addresses:
                continue
            device_options.append({"value": address, "label": address})

        if user_input is not None:
            updated_options = dict(self.config_entry.options)

            default_passcode_input = _coerce_passcode(
                user_input.get(CONF_DEFAULT_PASSCODE)
            )
            if default_passcode_input is not None:
                if not _is_valid_passcode(default_passcode_input):
                    errors[CONF_DEFAULT_PASSCODE] = "invalid_passcode"
                elif (
                    default_passcode_input
                    != self.config_entry.data.get(CONF_DEFAULT_PASSCODE)
                ):
                    hass.config_entries.async_update_entry(
                        self.config_entry,
                        data={
                            **self.config_entry.data,
                            CONF_DEFAULT_PASSCODE: default_passcode_input,
                        },
                    )

            remove_override = bool(user_input.get(CONF_REMOVE_OVERRIDE))
            selected_device = user_input.get(CONF_DEVICE_ADDRESS)
            device_passcode_input = _coerce_passcode(
                user_input.get(CONF_DEVICE_PASSCODE)
            )

            if selected_device is None:
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
                else:
                    errors[CONF_DEVICE_PASSCODE] = "passcode_required"

            if not errors:
                if overrides_changed:
                    if updated_overrides:
                        updated_options[CONF_DEVICE_PASSCODES] = updated_overrides
                    else:
                        updated_options.pop(CONF_DEVICE_PASSCODES, None)
                else:
                    updated_overrides = existing_overrides
                return self.async_create_entry(title="", data=updated_options)

        schema_dict: dict[vol.Marker, object] = {
            vol.Optional(CONF_DEFAULT_PASSCODE): PASSCODE_SELECTOR,
        }

        if device_options:
            schema_dict[vol.Optional(CONF_DEVICE_ADDRESS)] = SelectSelector(
                SelectSelectorConfig(
                    options=device_options,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
            schema_dict[vol.Optional(CONF_DEVICE_PASSCODE)] = PASSCODE_SELECTOR
            schema_dict[vol.Optional(CONF_REMOVE_OVERRIDE, default=False)] = (
                BooleanSelector()
            )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema_dict),
            errors=errors,
        )
