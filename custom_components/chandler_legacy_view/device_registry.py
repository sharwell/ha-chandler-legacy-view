"""Helpers for updating Home Assistant's device registry."""
from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN


def async_update_device_serial_number(
    hass: HomeAssistant, address: str, serial_number: str | None
) -> None:
    """Update the stored serial number for a valve if it has changed."""

    device_registry = dr.async_get(hass)
    device_entry = device_registry.async_get_device(identifiers={(DOMAIN, address)})
    if device_entry is None:
        return

    if device_entry.serial_number == serial_number:
        return

    device_registry.async_update_device(device_entry.id, serial_number=serial_number)


def async_update_device_sw_version(
    hass: HomeAssistant, address: str, sw_version: str | None
) -> None:
    """Update the stored firmware version for a valve if it has changed."""

    device_registry = dr.async_get(hass)
    device_entry = device_registry.async_get_device(identifiers={(DOMAIN, address)})
    if device_entry is None:
        return

    if device_entry.sw_version == sw_version:
        return

    device_registry.async_update_device(device_entry.id, sw_version=sw_version)
