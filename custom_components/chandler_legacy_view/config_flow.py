"""Config flow for the Chandler Legacy View integration."""

from __future__ import annotations

import functools
import logging
import re
from collections.abc import Mapping
from typing import Any, Awaitable, Callable, ParamSpec, TypeVar

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
    DEFAULT_VALVE_PASSCODE,
    DOMAIN,
)
from .entity import friendly_name_from_advertised_name


_LOGGER = logging.getLogger(__name__)

_P = ParamSpec("_P")
_R = TypeVar("_R")

_SENSITIVE_USER_INPUT_KEYS = frozenset({
    CONF_DEFAULT_PASSCODE,
    CONF_DEVICE_PASSCODE,
})


def _redact_user_input(user_input: object | None) -> object | None:
    """Redact sensitive data from configuration flow logging."""

    if not isinstance(user_input, Mapping):
        return user_input

    redacted: dict[Any, Any] = {}
    for key, value in user_input.items():
        if key in _SENSITIVE_USER_INPUT_KEYS:
            redacted[key] = "***REDACTED***"
        else:
            redacted[key] = value
    return redacted


def _log_config_flow_exceptions(
    step_id: str,
) -> Callable[[Callable[_P, Awaitable[_R]]], Callable[_P, Awaitable[_R]]]:
    """Return a decorator that logs unexpected exceptions for a flow step."""

    def decorator(function: Callable[_P, Awaitable[_R]]) -> Callable[_P, Awaitable[_R]]:
        @functools.wraps(function)
        async def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            user_input: object | None = None

            if len(args) >= 2:
                user_input = args[1]
            if "user_input" in kwargs:
                user_input = kwargs["user_input"]

            try:
                return await function(*args, **kwargs)
            except Exception:  # pragma: no cover - defensive logging
                _LOGGER.exception(
                    "Unexpected error during config flow step '%s' with input %s",
                    step_id,
                    _redact_user_input(user_input),
                )
                raise

        return wrapper

    return decorator


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

    @_log_config_flow_exceptions("user")
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

    @_log_config_flow_exceptions("init")
    async def async_step_init(
        self, user_input: dict[str, object] | None = None
    ) -> FlowResult:
        """Handle the default options step."""

        return await self._async_step_init_impl(user_input)

    async def _async_step_init_impl(
        self, user_input: dict[str, object] | None = None
    ) -> FlowResult:
        """Handle the default options step implementation."""

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

        for address in existing_overrides:
            if address in seen_addresses:
                continue
            device_options.append({"value": address, "label": address})

        if user_input is not None:
            updated_options = dict(self._config_entry.options)

            default_passcode_input = _coerce_passcode(
                user_input.get(CONF_DEFAULT_PASSCODE)
            )
            if default_passcode_input is not None:
                if not _is_valid_passcode(default_passcode_input):
                    errors[CONF_DEFAULT_PASSCODE] = "invalid_passcode"
                elif (
                    default_passcode_input
                    != self._config_entry.data.get(
                        CONF_DEFAULT_PASSCODE, DEFAULT_VALVE_PASSCODE
                    )
                ):
                    hass.config_entries.async_update_entry(
                        self._config_entry,
                        data={
                            **self._config_entry.data,
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
            schema_dict[vol.Optional(CONF_REMOVE_OVERRIDE, default=False)] = (
                BooleanSelector()
            )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema_dict),
            errors=errors,
        )

    @_log_config_flow_exceptions("select_shade")
    async def async_step_select_shade(
        self, user_input: dict[str, object] | None = None
    ) -> FlowResult:
        """Handle a request to select a shade to edit."""

        _LOGGER.debug(
            "Handling select_shade step using init fallback with input: %s",
            _redact_user_input(user_input),
        )
        return await self._async_step_init_impl(user_input)

    @_log_config_flow_exceptions("edit_shade")
    async def async_step_edit_shade(
        self, user_input: dict[str, object] | None = None
    ) -> FlowResult:
        """Handle editing a shade configuration entry."""

        _LOGGER.debug(
            "Handling edit_shade step using init fallback with input: %s",
            _redact_user_input(user_input),
        )
        return await self._async_step_init_impl(user_input)
