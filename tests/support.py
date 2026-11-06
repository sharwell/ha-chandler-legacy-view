"""Shared external Bluetooth boundary; no adapters, proxies, or sockets are opened."""

from fnmatch import fnmatchcase

from bleak.backends.device import BLEDevice
from homeassistant.components.bluetooth import (
    BluetoothChange,
    BluetoothServiceInfoBleak,
)

MODERN_422 = bytes.fromhex("3a070100082aae00010203010422")


def raw_advertisement(*segments: bytes) -> bytes:
    return b"\x02\x01\x06" + b"".join(
        bytes((len(segment) + 1, 0xFF)) + segment for segment in segments
    )


class BluetoothIO:
    """Deliver scanner observations and expiry through registered public callbacks."""

    def __init__(self):
        self.advertisement_callbacks = []
        self.unavailable_callbacks = []
        self.history = {}

    def register(self, hass, callback, matcher, scanning_mode):
        registration = (callback, matcher, scanning_mode)
        self.advertisement_callbacks.append(registration)
        return lambda: self.advertisement_callbacks.remove(registration)

    def track_unavailable(self, hass, callback, address, connectable=True):
        registration = (callback, address, connectable)
        self.unavailable_callbacks.append(registration)
        return lambda: self.unavailable_callbacks.remove(registration)

    def service_info(self, address="valve-a", *, name="CS_Meter_Soft", rssi=-50,
                     payload=MODERN_422, raw=None, manufacturer_data=None):
        return BluetoothServiceInfoBleak(
            name=name, address=address, rssi=rssi,
            manufacturer_data=({1850: payload[2:]} if manufacturer_data is None
                               else manufacturer_data),
            service_data={}, service_uuids=[], source="simulated-scanner",
            device=BLEDevice(address, name, {}), advertisement=None,
            connectable=True, time=0.0, tx_power=None, raw=raw,
        )

    def queued_advertisement(self, info):
        callbacks = [callback for callback, matcher, _ in self.advertisement_callbacks
                     if fnmatchcase(info.name, matcher["local_name"])]
        return lambda: [callback(info, BluetoothChange.ADVERTISEMENT)
                        for callback in callbacks]

    def advertise(self, address="valve-a", **kwargs):
        info = self.service_info(address, **kwargs)
        self.history[address] = info
        self.queued_advertisement(info)()
        return info

    def queued_loss(self, address="valve-a"):
        callbacks = [callback for callback, watched, connectable
                     in self.unavailable_callbacks if watched == address and connectable]
        info = self.history.get(address, self.service_info(address))
        return lambda: [callback(info) for callback in callbacks]

    def expire(self, address="valve-a"):
        self.queued_loss(address)()
