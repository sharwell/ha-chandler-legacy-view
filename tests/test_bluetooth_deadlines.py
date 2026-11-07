"""Bounded recovery retains ownership of temporarily uncooperative GATT work."""

import asyncio

import pytest

from ble import NOTIFY
from packets import dashboard_frames
from custom_components.chandler_legacy_view.const import CONNECTION_ATTEMPT_BUDGET_SECONDS
from test_evb034_authentication import setup_valve


@pytest.mark.parametrize("operation", ["connect", "start_notify", "services", "device_list", "authenticate", "dashboard", "stop_notify"])
@pytest.mark.parametrize("resistant", [False, True], ids=["cooperative", "late-completion"])
async def test_stalled_io_cannot_publish_or_overlap_a_replacement(
    connection_factory, transport, clock, operation, resistant,
):
    connection = connection_factory()
    valve = transport.valve()
    valve.legacy_service_discovery = operation == "services"
    valve.dashboards.extend([dashboard_frames(gallons=111), dashboard_frames(gallons=987)])
    gate = valve.operations[operation].block()
    gate.ignore_cancellation = resistant
    poll = asyncio.create_task(connection.async_poll())
    await clock.run(gate.started.wait())
    old_handler = valve.clients[0].notification_history[-1] if valve.clients and valve.clients[0].notification_history else None
    start = clock.loop.time()
    await clock.run(poll, budget=CONNECTION_ATTEMPT_BUDGET_SECONDS)
    assert clock.loop.time() - start < CONNECTION_ATTEMPT_BUDGET_SECONDS
    assert gate.cancelled
    assert connection.last_success is None
    assert connection.dashboard_data is None
    assert not connection.authentication_lockout
    if resistant:
        # Disconnect alone cannot make outstanding old GATT work safe to
        # forget. Another valve can still operate while this one is fenced.
        await clock.advance(31)
        await connection.async_poll()
        assert transport.attempts == ["valve-a"]
        other = connection_factory("valve-b")
        await clock.run(other.async_poll())
        assert other.last_success is not None
    if old_handler is not None:
        for frame in dashboard_frames(gallons=555):
            old_handler(NOTIFY, bytearray(frame))
        assert connection.dashboard_data is None
    gate.release.set()
    await clock.drain()
    # A late connect result is disconnected without ever becoming a session
    # or parsing metadata. A late operation never becomes a successful read.
    await clock.until(lambda: not connection.cleanup_pending)
    assert connection.last_success is None
    assert connection.dashboard_data is None
    assert all(not client.is_connected for client in valve.clients)
    valve.dashboards.clear()
    valve.dashboards.append(dashboard_frames(gallons=987))
    await clock.advance(31)
    await clock.run(connection.async_poll())
    assert connection.dashboard_data.water_usage == 987
    assert connection.last_success is not None
    assert len(transport.attempts) == (3 if resistant else 2)


async def test_persistent_request_stall_preserves_last_success_and_then_recovers(
    connection_factory, transport, clock,
):
    connection = connection_factory()
    await connection.async_set_persistent_connection_enabled(True)
    await clock.run(connection.async_poll())
    original_success = connection.last_success
    original_data = connection.dashboard_data
    valve = transport.valve()
    gate = valve.operations["dashboard"].block()
    gate.started.clear()
    gate.ignore_cancellation = True
    await clock.run(gate.started.wait())
    await clock.until(lambda: connection.cleanup_pending)
    await clock.advance(31)
    await connection.async_poll()
    assert transport.attempts == ["valve-a"]
    assert connection.last_success == original_success
    assert connection.dashboard_data is original_data
    gate.release.set()
    await clock.until(lambda: not connection.cleanup_pending)
    valve.dashboards.append(dashboard_frames(gallons=987))
    await clock.run(connection.async_poll())
    assert connection.dashboard_data.water_usage == 987
    assert connection.last_success > original_success
    await clock.run(connection.async_set_persistent_connection_enabled(False))


@pytest.mark.parametrize("operation", ["identify", "authenticate", "start_notify", "stop_notify"])
async def test_evb034_late_io_cannot_authenticate_or_update_an_expired_session(
    hass, runtime_factory, bluetooth_io, transport, clock, operation,
):
    _, connection, valve, _ = await setup_valve(hass, runtime_factory, bluetooth_io, transport)
    gate = valve.operations[operation].block()
    gate.ignore_cancellation = True
    await clock.run(connection.async_poll())
    success = connection.last_success
    data = connection.dashboard_data
    assert gate.cancelled
    assert connection.cleanup_pending
    assert not valve.clients[0].is_connected
    await clock.advance(31)
    await connection.async_poll()
    assert transport.attempts == ["valve-a"]
    # Expired callbacks cannot revive access or change HA's readings, even if
    # a backend delivers a complete old packet after closing the connection.
    from evb034_packets import frames
    for handler in valve.clients[0].notification_history:
        for frame in frames({"as": 2, "dwu": 99900}):
            handler(None, bytearray(frame))
    gate.release.set()
    await clock.until(lambda: not connection.cleanup_pending)
    assert connection.last_success == success
    assert connection.dashboard_data is data
    await clock.run(connection.async_poll())
    assert connection.last_success is not None
    assert connection.dashboard_data.water_usage == 5.67
    assert valve.packet_requests == []
