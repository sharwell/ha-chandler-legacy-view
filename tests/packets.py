MODERN_422 = bytes.fromhex("3a070100082aae00010203040422")
LEGACY_310 = bytes.fromhex("3a070100082a0102040310")
LEGACY_411 = bytes.fromhex("3a070100082a010203040411")
TWIN_122 = bytes.fromhex("3a070100082aae64010203040122")
EVB034_622 = bytes.fromhex("3a07e000082a04020622")

# Captured before/after the registry and sensor changes. These contain no PINs.
LOGGED_SHORT_ADVERTISEMENTS = (
    "02010609ff3a070100081500000e0943535f4d657465725f536f6674",
    "02010609ff3a070100081600000e0943535f4d657465725f536f6674",
    "02010609ff3a070100081900000d0943535f42575f46696c746572",
    "02010609ff3a070100081700000e0943535f4d657465725f536f6674",
    "02010609ff3a070100081a00000d0943535f42575f46696c746572",
    "02010608ff3a07191a1b1c1d0d0943535f42575f46696c746572",
    "02010609ff3a0701000527ae000d0943535f42575f46696c746572",
    "02010609ff3a0701000528af000d0943535f42575f46696c746572",
)
FOREIGN_MANUFACTURER_ADVERTISEMENT = bytes.fromhex(
    "02010609ff59000100052552000e0943535f4d657465725f536f6674"
)

# Synthetic reference requests, calculated with polynomial long division rather
# than the production encoder. Each row is PIN, challenge, and complete request.
# For row 1 (polynomial 0x11d), the successive remainders are
# 92, 54, ff, 39, 85; XOR with digits 1, 2, 3, 4 gives 55, fd, 3a, 81.
# These are arithmetic protocol examples, not captured hardware evidence.
AUTHENTICATION_VECTORS = (
    ("1234", 23, bytes.fromhex("747450411da55a55fd3a81010203040506070809")),
    ("1357", 0, bytes.fromhex("74745041872345f19c867b010203040506070809")),
    ("0001", 255, bytes.fromhex("747450419bff7ea0cfe2e9010203040506070809")),
    ("9999", 254, bytes.fromhex("747450413901a243eeaec9010203040506070809")),
)


def raw_advertisement(*segments: bytes) -> bytes:
    """Wrap company-prefixed payloads in Bluetooth manufacturer AD structures."""

    return b"\x02\x01\x06" + b"".join(
        bytes((len(segment) + 1, 0xFF)) + segment for segment in segments
    )


def device_list_frame(*, status: int, counter: int = 23,
                      serial: bytes = b"\x01\x02\x03\x04") -> bytes:
    """Build a synthetic EVB019 response without using a device's private data."""

    packet = bytearray(20)
    packet[0:2] = b"tt"
    packet[7] = status
    packet[11] = counter
    packet[13:17] = serial
    return bytes(packet)


def classic_device_list_frame(*, status: int = 112) -> bytes:
    """Encode a synthetic legacy PIN, 1357, using the classic wire layout."""

    packet = bytearray(device_list_frame(status=status))
    offset = status - 112
    packet[8:12] = bytes(
        (offset + 16, (offset + 15) * 2, (offset + 12) * 3, (offset + 7) * 4)
    )
    return bytes(packet)


def dashboard_frames(*, hour=7, minute=23, gallons=567, is_pm=False) -> list[bytes]:
    """Return a complete synthetic dashboard reporting 7:23 and 567 gallons."""

    first, second, third = (bytearray(20) for _ in range(3))
    first[0:5] = b"uu\x00" + bytes((hour, minute))
    first[5] = int(is_pm)
    first[11:13] = gallons.to_bytes(2, "big")
    first[-1] = 57
    second[0:3] = b"uu\x01"
    second[-1] = 58
    third[0:3] = b"uu\x02"
    return [bytes(first), bytes(second), bytes(third), bytes(20), bytes(20), bytes(5) + b":"]
