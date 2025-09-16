"""Base entities for the Chandler Legacy View integration."""

from __future__ import annotations

from homeassistant.helpers.entity import DeviceInfo, Entity

from .const import (
    DEFAULT_FRIENDLY_NAME,
    DEFAULT_MANUFACTURER,
    DISCOVERY_VIA_DEVICE_ID,
    DOMAIN,
    FRIENDLY_NAME_OVERRIDES,
)
from .models import ValveAdvertisement


def friendly_name_from_advertised_name(advertised_name: str | None) -> str:
    """Return a friendly valve name for a Bluetooth advertised local name."""

    if not advertised_name:
        return DEFAULT_FRIENDLY_NAME

    normalized_name = advertised_name.strip().casefold()
    if not normalized_name:
        return DEFAULT_FRIENDLY_NAME

    return FRIENDLY_NAME_OVERRIDES.get(normalized_name, DEFAULT_FRIENDLY_NAME)


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
            model=self._advertisement.model,
            via_device=(DOMAIN, DISCOVERY_VIA_DEVICE_ID),
            sw_version=self._format_firmware_version(self._advertisement),
        )

    def async_update_from_advertisement(self, advertisement: ValveAdvertisement) -> None:
        """Store the most recent advertisement seen for this valve."""

        self._advertisement = advertisement
        self._attr_name = self._compute_name(advertisement)

    def _compute_name(self, advertisement: ValveAdvertisement) -> str:
        """Generate a user-friendly name for the valve entity."""

        return friendly_name_from_advertised_name(advertisement.name)

    def _format_firmware_version(self, advertisement: ValveAdvertisement) -> str | None:
        """Format the firmware version reported by the advertisement."""

        if (
            advertisement.firmware_major is None
            or advertisement.firmware_minor is None
        ):
            return None
        return f"{advertisement.firmware_major}.{advertisement.firmware_minor:02d}"
