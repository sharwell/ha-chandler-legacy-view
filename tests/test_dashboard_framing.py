"""Malformed Dashboard notifications must not replace published readings."""

import pytest
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.helpers import entity_registry as er

from custom_components.chandler_legacy_view.const import DATA_CONNECTION_MANAGER, DOMAIN
from packets import dashboard_frames


@pytest.mark.parametrize("packet_index,defect", [
    *((index, defect) for index in range(6) for defect in ("short", "long")),
    *((index, "terminator") for index in (0, 1, 5)),
    *((index, defect) for index in range(3) for defect in ("prefix", "index")),
])
async def test_malformed_response_retains_states_and_success_until_valid_recovery(
    hass, runtime_factory, bluetooth_io, transport, clock, packet_index, defect,
):
    # CsDashboardPacket.updatePacket requires 20-byte headers/graph fragments
    # and a six-byte tail. Keeping the terminator while removing a data byte
    # catches the bug where marker 39/3A became flags, tank, or usage data.
    entry = await runtime_factory()
    bluetooth_io.advertise()
    await hass.async_block_till_done()
    current = hass.data[DOMAIN][entry.entry_id][DATA_CONNECTION_MANAGER].get_connection("valve-a")
    valve = transport.valve()
    valve.dashboards.append(dashboard_frames(gallons=135))
    await clock.run(current.async_poll())
    # State writes can precede event-listener delivery. Drain HA work before
    # observing the next poll so the sequence describes completed publications.
    await hass.async_block_till_done()
    usage = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, "valve-a_water_usage_today")
    assert hass.states.get(usage).state == "135"
    original_data = current.dashboard_data
    original_success = current.last_success
    states = []
    remove = hass.bus.async_listen(EVENT_STATE_CHANGED, lambda event: states.append(
        event.data["new_state"].state) if event.data["entity_id"] == usage else None)
    try:
        frames = dashboard_frames(gallons=987)
        packet = bytearray(frames[packet_index])
        if defect == "short":
            del packet[-2]
        elif defect == "long":
            packet.insert(len(packet) - 1, 0)
        elif defect == "terminator":
            packet[-1] = 0
        elif defect == "prefix":
            packet[0] = 0
        else:
            packet[2] = 3
        frames[packet_index] = bytes(packet)
        valve.dashboards.append(frames)
        await clock.advance(31)
        await clock.run(current.async_poll())
        await hass.async_block_till_done()
        assert hass.states.get(usage).state == "135"
        assert current.dashboard_data == original_data
        assert current.last_success == original_success
        assert states == []
        valve.dashboards.append(dashboard_frames(gallons=246))
        await clock.advance(31)
        await clock.run(current.async_poll())
        await hass.async_block_till_done()
        assert hass.states.get(usage).state == "246"
        assert current.last_success > original_success
        assert states == ["246"]
    finally:
        remove()


async def test_graph_values_that_look_like_headers_remain_usage_samples(
    connection_factory, transport, clock,
):
    # Once graph assembly starts, the app consumes samples by fragment length,
    # not by their values. A raw graph is allowed to begin with 75 75 00/01.
    frames = dashboard_frames()
    fourth, fifth, tail = map(bytearray, frames[3:])
    fourth[:3] = b"uu\x00"
    fifth[:3] = b"uu\x01"
    tail[:3] = b"uu\x02"
    frames[3:] = map(bytes, (fourth, fifth, tail))
    current = connection_factory()
    transport.valve().dashboards.append(frames)
    await clock.run(current.async_poll())
    assert current.dashboard_data is not None
    expected = frames[2][3:] + frames[3] + frames[4] + frames[5][:5]
    assert current.dashboard_data.graph_usage_ten_gallons == tuple(expected)
    assert len(expected) == 62
    assert current.last_success is not None
