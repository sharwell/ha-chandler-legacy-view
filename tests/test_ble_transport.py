"""Wire examples keep the shared valve fake from accepting arbitrary commands."""

import pytest

from ble import NOTIFY, WRITE
from packets import AUTHENTICATION_VECTORS, device_list_frame


@pytest.mark.parametrize("pin,counter,payload", AUTHENTICATION_VECTORS)
async def test_reference_authentication_grants_dashboard_only_on_its_session(bluetooth_io, transport, pin, counter, payload):
    info = bluetooth_io.advertise()
    valve = transport.valve()
    valve.pin = pin
    valve.connection_counter = counter
    client = await transport.connect(None, info.device, info.name)
    received = []
    await client.start_notify(NOTIFY, lambda _, packet: received.append(bytes(packet)))
    await client.write_gatt_char(WRITE, b"u" * 20, response=True)
    assert received == []
    await client.write_gatt_char(WRITE, b"t" * 20, response=True)
    assert received.pop() == device_list_frame(status=0, counter=counter)
    await client.write_gatt_char(WRITE, payload, response=True)
    assert received.pop() == device_list_frame(status=128, counter=(counter + 1) & 255)
    await client.write_gatt_char(WRITE, b"u" * 20, response=True)
    assert len(received) == 6
    await client.disconnect()

    replacement = await transport.connect(None, info.device, info.name)
    received.clear()
    await replacement.start_notify(NOTIFY, lambda _, packet: received.append(bytes(packet)))
    await replacement.write_gatt_char(WRITE, b"u" * 20, response=True)
    assert received == []
    # The old request must not authenticate the next session's fresh challenge.
    await replacement.write_gatt_char(WRITE, b"t" * 20, response=True)
    assert received.pop() == device_list_frame(status=0, counter=(counter + 1) & 255)
    await replacement.write_gatt_char(WRITE, payload, response=True)
    assert received.pop()[7] == 0
    await replacement.write_gatt_char(WRITE, b"u" * 20, response=True)
    assert received == []
    await replacement.disconnect()


@pytest.mark.parametrize("defect", ["short", "long", "zero-filled", "wrong-pin", "wrong-challenge", "no-challenge"])
async def test_invalid_authentication_cannot_obtain_protected_readings(bluetooth_io, transport, defect):
    info = bluetooth_io.advertise()
    valve = transport.valve()
    payload = AUTHENTICATION_VECTORS[0][2]
    if defect == "short":
        payload = payload[:-1]
    elif defect == "long":
        payload += b"\x01"
    elif defect == "zero-filled":
        payload = b"ttPA" + bytes(16)
    elif defect == "wrong-pin":
        valve.pin = "2468"
    elif defect == "wrong-challenge":
        valve.connection_counter = 24
    # Even a scripted success may not override an invalid outbound request.
    valve.auth_replies.append([device_list_frame(status=128)])
    client = await transport.connect(None, info.device, info.name)
    received = []
    await client.start_notify(NOTIFY, lambda _, packet: received.append(bytes(packet)))
    if defect != "no-challenge":
        await client.write_gatt_char(WRITE, b"t" * 20, response=True)
        received.clear()
    await client.write_gatt_char(WRITE, payload, response=True)
    assert received.pop()[7] == 0
    await client.write_gatt_char(WRITE, b"u" * 20, response=True)
    assert received == []
    await client.disconnect()
