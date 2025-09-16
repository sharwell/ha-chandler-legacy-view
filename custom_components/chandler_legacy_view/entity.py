"""Base entities for the Chandler Legacy View integration."""

from __future__ import annotations

from homeassistant.helpers.entity import DeviceInfo, Entity

from .const import DEFAULT_DEVICE_NAME, DEFAULT_MANUFACTURER, DOMAIN
from .models import ValveAdvertisement


class ChandlerValveEntity(Entity):
    """Base entity shared by Chandler Legacy valve platforms."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, advertisement: ValveAdvertisement) -> None:
        """Initialize the entity."""

        self._advertisement = advertisement
        self._attr_unique_id = advertisement.address
        self._attr_name = self._compute_name(advertisement)

    @property
    def device_info(self) -> DeviceInfo:
        """Return metadata for the device registry."""

        return DeviceInfo(
            identifiers={(DOMAIN, self._advertisement.address)},
            name=self._compute_name(self._advertisement),
            manufacturer=DEFAULT_MANUFACTURER,
            via_device=(DOMAIN, "bluetooth"),
        )

    def async_update_from_advertisement(self, advertisement: ValveAdvertisement) -> None:
        """Store the most recent advertisement seen for this valve."""

        self._advertisement = advertisement
        self._attr_name = self._compute_name(advertisement)

    def _compute_name(self, advertisement: ValveAdvertisement) -> str:
        """Generate a user-friendly name for the valve entity."""

        if advertisement.name:
            return advertisement.name

        address_fragment = advertisement.address.replace(":", "")[-4:]
        return f"{DEFAULT_DEVICE_NAME} {address_fragment.upper()}"
