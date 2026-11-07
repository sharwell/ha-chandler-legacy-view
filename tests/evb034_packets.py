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
