"""Regeneration and backwash action buttons for Chandler valves."""

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .connection import ValveConnection
from .cycle import CycleAction, supports_soak
from .cycle_entity import ValveCycleEntity, async_add_cycle_entities
from .models import ValveAdvertisement


class ValveCycleButton(ValveCycleEntity, ButtonEntity):
    """Submit exactly one identified cycle action when pressed."""

    def __init__(
        self, advertisement: ValveAdvertisement, connection: ValveConnection, action: CycleAction
    ) -> None:
        """Bind a start, advance, or next-scheduled-time action to the valve."""
        super().__init__(advertisement, connection, action.value, "Advance to next cycle step")
        self._action = action

    @property
    def name(self) -> str:
        """Use the program's Regenerate or Backwash wording for start and schedule."""
        if self._action is CycleAction.ADVANCE:
            return self._attr_name
        verb = "Regenerate" if supports_soak(self._connection.entity_capabilities) else "Backwash"
        suffix = "now" if self._action is CycleAction.START else "at next scheduled time"
        return f"{verb} {suffix}"

    @property
    def supported(self) -> bool:
        """Keep the button limited to applicable valve programs."""
        return super().supported and self._connection.cycle_action_supported(self._action)

    @property
    def available(self) -> bool:
        """Show eligibility from the last confirmed cycle, pending a fresh check."""
        return self.supported and self._connection.cycle_action_available(self._action)

    async def async_added_to_hass(self) -> None:
        """Refresh button availability when authentication lockout changes."""
        await super().async_added_to_hass()
        self.async_on_remove(self._connection.add_authentication_listener(self._publish))

    async def async_press(self) -> None:
        """Send the button's action, propagating rejection or exchange errors."""
        await self._connection.async_cycle_action(self._action)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Register supported operation buttons on each valve device."""
    async_add_cycle_entities(
        hass, entry, async_add_entities,
        lambda advertisement, connection: [
            ValveCycleButton(advertisement, connection, action) for action in CycleAction
        ],
    )
