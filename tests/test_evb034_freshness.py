"""The selected Evb034 policy confirms session health for observed baselines."""

import asyncio
from datetime import datetime

import pytest
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from evb034_packets import frames
from test_evb034_entities import setup_readings, sensor_id
from test_polling_policy import refresh_action


async def session(hass, entry_factory, runtime_factory, bluetooth_io, transport, clock,
                  *, firmware=622, clack=False, interval=10, persistent=True, initial=None):
    entry, connection, valve = await setup_readings(
        hass, entry_factory, runtime_factory, bluetooth_io, transport,
        firmware=firmware, dlic=int(clack),
    )
    await connection.async_set_persistent_poll_interval(interval)
    if persistent:
        await connection.async_set_persistent_connection_enabled(True)
    valve.dashboards.append(frames(initial if initial is not None else {"dwu": 12300, "gpf": 150}))
    await clock.run(connection.async_poll())
    valve.stream_enabled = False
    await clock.drain()
    return entry, connection, valve, valve.clients[-1]


async def receive(owner, packet, clock):
    """Deliver complete wire frames and wait for their production ACK writes."""
    payloads = frames(packet)
    acknowledged = sum(payload == b"\xcc" for client, payload in owner.valve.writes if client is owner)
    for payload in payloads:
        owner.notify(payload)
    received = clock.utcnow()
    await clock.until(lambda: sum(payload == b"\xcc" for client, payload in owner.valve.writes if client is owner)
                      >= acknowledged + len(payloads), budget=1)
    return received


async def age_from(clock, received, age):
    await clock.advance(max(0, age - (clock.utcnow() - received).total_seconds()))


@pytest.mark.parametrize("firmware", [600, 608, 609, 610, 611, 622])
@pytest.mark.parametrize("clack", [False, True])
async def test_any_valid_reading_renews_only_baselined_fields_in_the_same_session(
    hass, entry_factory, runtime_factory, bluetooth_io, transport, clock, firmware, clack,
):
    _, connection, valve, owner = await session(
        hass, entry_factory, runtime_factory, bluetooth_io, transport, clock,
        firmware=firmware, clack=clack, initial={"dwu": 0, "gpf": 0},
    )
    flow = sensor_id(hass, "present_flow")
    hardness = sensor_id(hass, "water_hardness")
    registry_ids = {record.entity_id for record in er.async_get(hass).entities.values()}
    for _ in range(3):
        await clock.advance(90)
        # A repeated valid zero is live reading traffic. It intentionally
        # confirms the retained flow baseline without retransmitting gpf.
        await receive(owner, {"dwu": 0}, clock)
        assert float(hass.states.get(flow).state) == 0
        assert hass.states.get(hardness).state == "unavailable"
    await receive(owner, {"dwh": 20}, clock)
    assert hass.states.get(hardness).state == "20"
    assert {record.entity_id for record in er.async_get(hass).entities.values()} == registry_ids
    assert valve.clients == [owner] and owner.is_connected
    assert all(payload not in (b"t" * 20, b"u" * 20) for _, payload in valve.writes)


@pytest.mark.parametrize("packet", [
    {"gpf": None}, {"gpf": True}, {"gpf": "invalid"}, {"gpf": -1},
    {"gpf": 2**40}, {"dh": 99}, {"dm": 60}, {"dlf": 622},
    {"gbre": 1}, {"other": "metadata"},
])
async def test_null_invalid_metadata_and_status_traffic_cannot_keep_a_baseline_fresh(
    hass, entry_factory, runtime_factory, bluetooth_io, transport, clock, packet,
):
    _, connection, _, owner = await session(
        hass, entry_factory, runtime_factory, bluetooth_io, transport, clock,
    )
    usage = sensor_id(hass, "water_usage_today")
    original_confirmation = connection.last_success
    for _ in range(2):
        await clock.advance(40)
        await receive(owner, packet, clock)
        owner.notify(b"\xcc")
        owner.notify(b"\xe0")
        await clock.drain()
    await age_from(clock, original_confirmation, 119)
    assert float(hass.states.get(sensor_id(hass, "present_flow")).state) == 1.5
    await age_from(clock, original_confirmation, 120.1)
    assert hass.states.get(usage).state == "unavailable"
    assert hass.states.get(sensor_id(hass, "present_flow")).state == "unavailable"
    assert connection.last_success == original_confirmation
    assert owner.is_connected


@pytest.mark.parametrize("interval", [10, 300])
async def test_silence_expires_then_one_new_update_restores_snapshot_atomically(
    hass, entry_factory, runtime_factory, bluetooth_io, transport, clock, refresh_action, interval,
):
    _, connection, valve, owner = await session(
        hass, entry_factory, runtime_factory, bluetooth_io, transport, clock, interval=interval,
    )
    usage = sensor_id(hass, "water_usage_today")
    original = connection.last_success
    maximum_age = max(3 * interval, 120)
    await age_from(clock, original, maximum_age - 1)
    assert float(hass.states.get(usage).state) == 123
    changes = []
    @callback
    def record(event):
        if event.data["entity_id"] == usage:
            changes.append(event.data["new_state"].state)
    remove = hass.bus.async_listen(EVENT_STATE_CHANGED, record)
    try:
        await age_from(clock, original, maximum_age + 0.1)
        assert hass.states.get(usage).state == "unavailable"
        await connection.async_set_persistent_poll_interval(300)
        assert hass.states.get(usage).state == "unavailable"
        manual = asyncio.create_task(refresh_action(usage))
        await clock.drain()
        await receive(owner, {"dwu": 98700}, clock)
        await clock.run(manual)
        assert [value if value == "unavailable" else float(value) for value in changes] == ["unavailable", 987]
        assert float(hass.states.get(sensor_id(hass, "present_flow")).state) == 1.5
        assert hass.states.get(sensor_id(hass, "battery")).state == "unavailable"
        assert valve.clients == [owner] and owner.is_connected
    finally:
        remove()


async def test_new_connection_cannot_renew_a_field_from_the_previous_baseline(
    hass, entry_factory, runtime_factory, bluetooth_io, transport, clock,
):
    _, connection, valve, old = await session(
        hass, entry_factory, runtime_factory, bluetooth_io, transport, clock, persistent=False,
    )
    old_handler = old.notification_history[0]
    assert not old.is_connected
    assert float(hass.states.get(sensor_id(hass, "present_flow")).state) == 1.5
    await clock.advance(31)
    valve.stream_enabled = True
    valve.dashboards.append(frames({"dwu": 98700}))
    await clock.run(connection.async_poll())
    assert float(hass.states.get(sensor_id(hass, "water_usage_today")).state) == 987
    assert hass.states.get(sensor_id(hass, "present_flow")).state == "unavailable"
    for payload in frames({"gpf": 999}):
        old_handler(None, bytearray(payload))
    await clock.drain()
    assert hass.states.get(sensor_id(hass, "present_flow")).state == "unavailable"
    assert len(valve.clients) == 2


@pytest.mark.parametrize("failure", ["access", "queue", "json"])
async def test_access_or_receiver_loss_invalidates_baselines_before_publication_tick(
    hass, entry_factory, runtime_factory, bluetooth_io, transport, clock, failure,
):
    _, connection, valve, owner = await session(
        hass, entry_factory, runtime_factory, bluetooth_io, transport, clock, interval=300,
    )
    usage = sensor_id(hass, "water_usage_today")
    old_handler = owner.notification_history[0]
    if failure == "queue":
        for _ in range(140):
            old_handler(None, bytearray(b"\xcc"))
    else:
        for payload in frames({"as": 1} if failure == "access" else b"{bad-json}"):
            owner.notify(payload)
    await clock.until(lambda: hass.states.get(usage).state == "unavailable", budget=1)
    for payload in frames({"as": 2, "dwu": 99900, "gpf": 999}):
        old_handler(None, bytearray(payload))
    await clock.drain()
    assert hass.states.get(usage).state == "unavailable"
    await clock.until(lambda: not owner.is_connected, budget=10)
    assert valve.clients == [owner]


@pytest.mark.parametrize("firmware", [600, 608, 609, 610, 611, 622])
@pytest.mark.parametrize("clack", [False, True])
async def test_clock_requires_all_components_for_its_firmware_before_becoming_eligible(
    hass, entry_factory, runtime_factory, bluetooth_io, transport, clock, firmware, clack,
):
    _, connection, _, owner = await session(
        hass, entry_factory, runtime_factory, bluetooth_io, transport, clock,
        firmware=firmware, clack=clack, initial={"dwu": 0},
    )
    clock_id = sensor_id(hass, "time_of_day")
    await receive(owner, {"dh": 23}, clock)
    assert hass.states.get(clock_id).state == "unavailable"
    await receive(owner, {"dm": 37}, clock)
    if firmware < 609 and not clack:
        assert hass.states.get(clock_id).state == "unavailable"
        await receive(owner, {"da": 1}, clock)
    value = dt_util.as_local(datetime.fromisoformat(hass.states.get(clock_id).state))
    assert (value.hour, value.minute) == (23, 37)


async def test_buffering_and_metadata_do_not_move_the_original_session_deadline(
    hass, entry_factory, runtime_factory, bluetooth_io, transport, clock,
):
    _, connection, _, owner = await session(
        hass, entry_factory, runtime_factory, bluetooth_io, transport, clock, interval=300,
    )
    await clock.advance(100)
    received = await receive(owner, {"dwu": 98700}, clock)
    await clock.advance(100)
    await receive(owner, {"gbre": 1}, clock)
    usage = sensor_id(hass, "water_usage_today")
    assert float(hass.states.get(usage).state) == 123
    await clock.until(lambda: float(hass.states.get(usage).state) == 987, budget=101)
    assert abs((connection.last_success - received).total_seconds()) < 0.1
    await age_from(clock, received, 899)
    assert float(hass.states.get(usage).state) == 987
    await age_from(clock, received, 900.1)
    assert hass.states.get(usage).state == "unavailable"
    assert owner.is_connected


async def test_stopping_a_healthy_session_publishes_buffered_data_without_retimestamping(
    hass, entry_factory, runtime_factory, bluetooth_io, transport, clock,
):
    _, connection, _, owner = await session(
        hass, entry_factory, runtime_factory, bluetooth_io, transport, clock, interval=300,
    )
    await clock.advance(20)
    received = await receive(owner, {"dwu": 98700}, clock)
    await clock.advance(20)
    usage = sensor_id(hass, "water_usage_today")
    assert float(hass.states.get(usage).state) == 123
    await clock.run(connection.async_set_persistent_connection_enabled(False))
    assert float(hass.states.get(usage).state) == 987
    assert abs((connection.last_success - received).total_seconds()) < 0.1
    assert not owner.is_connected


async def test_completed_on_demand_snapshot_expires_after_its_interval_and_grace(
    hass, entry_factory, runtime_factory, bluetooth_io, transport, clock,
):
    _, connection, _, owner = await session(
        hass, entry_factory, runtime_factory, bluetooth_io, transport, clock, persistent=False,
    )
    received = connection.last_success
    assert not owner.is_connected
    usage = sensor_id(hass, "water_usage_today")
    await age_from(clock, received, 1199)
    assert float(hass.states.get(usage).state) == 123
    await age_from(clock, received, 1200.1)
    assert hass.states.get(usage).state == "unavailable"
