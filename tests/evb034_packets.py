"""Synthetic C6.x wire fixtures independent of the production frame encoder."""

import json


def crc16(data):
    state = 0xFFFF
    for byte in data:
        state ^= byte << 8
        for _ in range(8):
            state = ((state << 1) ^ (0x1021 if state & 0x8000 else 0)) & 0xFFFF
    return state


def frames(packet, size=20):
    raw = (json.dumps(packet, separators=(",", ":")).encode()
           if isinstance(packet, dict) else packet)
    result = []
    while raw:
        part, raw = raw[:size - 3], raw[size - 3:]
        flag = (0x80 if not result else 0) | (0 if raw else 0x40)
        body = bytes((flag,)) + part
        result.append(body + crc16(body).to_bytes(2, "little"))
    return result


def password_pin(payload, counter):
    """Decode the shuffled C6.x PIN using polynomial long division."""
    if len(payload) != 20:
        return None
    polynomial, state, nonce = payload[1], payload[3], payload[5]
    if not polynomial:
        return None

    def compute(value):
        nonlocal state
        dividend = (state ^ value) << 8
        divisor = 0x100 | polynomial
        while dividend.bit_length() > 8:
            dividend ^= divisor << (dividend.bit_length() - 9)
        state = dividend
        return state

    previous = (counter ^ compute(nonce)) & 255
    digits = []
    for position in (7, 9, 11, 13):
        digits.append(payload[position] ^ compute(previous))
        previous = payload[position]
    if any(digit > 9 for digit in digits):
        return None
    return "".join(str(digits[index]) for index in (0, 2, 3, 1))
