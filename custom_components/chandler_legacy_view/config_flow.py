"""Config flow for the Chandler Legacy View integration."""

from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .const import DOMAIN


class ChandlerLegacyViewConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Chandler Legacy View."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, object] | None = None
    ) -> FlowResult:
        """Handle the initial step initiated by the user."""

        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=vol.Schema({}))

        return self.async_create_entry(title="Chandler Legacy View", data={})
