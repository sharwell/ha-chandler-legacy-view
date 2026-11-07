"""Passive comparisons with protocol decisions before the five alignment fixes.

The reference is commit 7ca17fa. This module never sends requests or provides a
fallback for production decisions. It records previous acceptance only while
the observed exchange is one that the previous code could also have followed.
"""

from __future__ import annotations

from collections.abc import Mapping

from .models import ValveAdvertisement


def password_digits(
    status: int, byte_a: int, byte_b: int, byte_c: int, byte_d: int,
    *, signed_bytes: bool,
) -> tuple[int, int, int, int]:
    """Decode the shared arithmetic with the selected byte interpretation."""

    def signed(value: int) -> int:
        return (value + 128) % 256 - 128

    if signed_bytes:
        byte_a, byte_b, byte_c, byte_d = map(signed, (byte_a, byte_b, byte_c, byte_d))
    offset = signed(status - 112)
    last = signed(int(byte_d / 4) - offset)
    third = signed(int(byte_c / 3) - last - offset)
    second = signed(int(byte_b / 2) - third - last - offset)
    first = signed(byte_a - second - third - last - offset)
    return first, second, third, last


class PreviousProtocol:
    """Track previous access without issuing speculative authentication writes."""

    def __init__(self) -> None:
        self.protocol = "unknown"
        self.authentication_required: bool | None = None
        self.advertised_firmware: int | None = None
        self.begin_poll()

    def begin_poll(self) -> None:
        """Require access evidence from the current exchange."""

        self.comparable = True
        self.access = False
        self.accepted_protocol: str | None = None
        self.accepted_digits: tuple[int, ...] | None = None
        self.counter: int | None = None
        self.last_status: int | None = None
        self.firmware_fields: tuple[int, int] | None = None
        self.actions_changed = False

    def observe_advertisement(self, advertisement: ValveAdvertisement) -> None:
        """Mirror the old format selection from independently known metadata."""

        self.advertised_firmware = advertisement.firmware_version
        self.authentication_required = advertisement.authentication_required
        if self.protocol != "unknown":
            return
        firmware = advertisement.firmware_version
        if advertisement.is_twin_valve:
            self.protocol = "counter"
        elif firmware is not None and firmware > 0:
            self.protocol = "classic" if firmware < 419 else "counter"
        elif (
            advertisement.has_connection_counter is True
            and advertisement.manufacturer_data_complete
        ):
            # An incomplete observation may carry capabilities learned only by
            # the new DeviceList decoder. They cannot prove previous success.
            self.protocol = "counter"

    def observe_device_list(self, packet: bytes) -> None:
        """Record access only if the previous decoder accepts this response."""

        self.last_status = packet[7]
        self.firmware_fields = (packet[5], packet[6])
        if self.protocol == "unknown":
            if packet[7] == 0 or packet[2] == 1:
                self.protocol = "counter"
            else:
                return
        digits = None
        if self.protocol == "classic":
            digits = password_digits(*packet[7:12], signed_bytes=False)
            accepted = (
                self.authentication_required is False
                and all(0 <= digit < 10 for digit in digits)
            )
        else:
            self.counter = packet[11] if packet[7] in (0, 128) else None
            accepted = packet[7] == 128
        if self.comparable and accepted and not self.access:
            self.access = True
            self.accepted_protocol = self.protocol
            self.accepted_digits = digits

    def before_authentication(self, counter: int) -> None:
        """Do not attribute success from a different request to the old code."""

        if self.protocol != "counter" or self.last_status != 0 or self.counter != counter:
            self.actions_changed = True
            self.comparable = False


def previous_dashboard_packet_index(
    packet: bytes, existing_packets: Mapping[int, bytes],
) -> int | None:
    """Reproduce the old collector solely to establish complete acceptance."""

    length = len(packet)
    if not 5 <= length <= 20:
        return None
    if packet[:2] == b"uu":
        index = packet[2]
        if index not in (0, 1, 2) or index in existing_packets:
            return None
        if index == 0 and (length < 19 or packet[-1] != 57):
            return None
        if index == 1 and (length < 19 or packet[-1] != 58):
            return None
        if index == 2 and length < 20:
            return None
        return index
    if 2 not in existing_packets:
        return None
    for index in range(3, 6):
        if index in existing_packets:
            continue
        if index in (3, 4) and length < 20:
            return None
        if index == 5 and packet[-1] != 58:
            return None
        return index
    return None
