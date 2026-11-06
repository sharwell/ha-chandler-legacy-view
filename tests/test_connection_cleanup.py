"""Cancellation and cleanup budgets through public APIs and gated Bluetooth I/O."""

import asyncio
import pytest
from custom_components.chandler_legacy_view.connection import ValveConnectionManager


def assert_closed(valve):
    assert len(valve.clients) == 1
    assert not valve.clients[0].is_connected
    assert valve.clients[0].disconnects == 1
    assert valve.clients[0].notifications == {}


async def test_task_creation_failure_keeps_cleanup_with_poll(hass, connection_factory, transport, clock, monkeypatch):
    connection = connection_factory()
    await connection.async_set_persistent_connection_enabled(True)
    create_task = hass.loop.create_task
    def fail_keepalive(coroutine, **kwargs):
        # This scenario specifically concerns task creation failure. Transport
        # failures cannot reproduce failure to create the handoff task itself.
        if coroutine.cr_code.co_name == "_async_persistent_keepalive_loop":
            raise RuntimeError("Task creation failed")
        return create_task(coroutine, **kwargs)
    monkeypatch.setattr(hass.loop, "create_task", fail_keepalive)
    with pytest.raises(RuntimeError, match="Task creation failed"):
        await clock.run(connection.async_poll())
    await clock.run(connection.async_unload())
    assert_closed(transport.valve())


async def test_cancelled_poll_keeps_ownership_until_disconnect(connection_factory, transport, clock):
    connection = connection_factory()
    valve = transport.valve()
    gate = valve.operations["disconnect"].block()
    poll = asyncio.create_task(connection.async_poll())
    unload = None
    try:
        await clock.run(gate.started.wait())
        poll.cancel()
        await clock.drain()
        await connection.async_poll()
        assert transport.attempts == ["valve-a"]
        unload = asyncio.create_task(connection.async_unload())
        await clock.drain()
        assert not poll.done() and not unload.done()
        assert valve.clients[0].is_connected
    finally:
        gate.release.set()
        tasks = [poll] if unload is None else [poll, unload]
        results = await clock.run(asyncio.gather(*tasks, return_exceptions=True))
    assert isinstance(results[0], asyncio.CancelledError)
    assert results[1] is None
    assert_closed(valve)


async def test_repeated_cancellation_drains_reset_and_disconnect(connection_factory, transport, clock):
    connection = connection_factory()
    valve = transport.valve()
    reset = valve.operations["reset"].block()
    disconnect = valve.operations["disconnect"].block()
    poll = asyncio.create_task(connection.async_poll())
    try:
        await clock.run(reset.started.wait())
        for _ in range(2):
            poll.cancel()
            await clock.drain()
            assert not poll.done()
            assert not disconnect.started.is_set()
            assert not reset.cancelled
        reset.release.set()
        await clock.run(disconnect.started.wait())
        for _ in range(2):
            poll.cancel()
            await clock.drain()
            assert not poll.done()
            assert valve.clients[0].is_connected
            assert not disconnect.cancelled
    finally:
        reset.release.set()
        disconnect.release.set()
        results = await clock.run(asyncio.gather(poll, return_exceptions=True))
    assert isinstance(results[0], asyncio.CancelledError)
    assert_closed(valve)


async def test_unload_during_normal_keepalive_teardown(connection_factory, transport, clock):
    connection = connection_factory()
    await connection.async_set_persistent_connection_enabled(True)
    await connection.async_poll()
    valve = transport.valve()
    gate = valve.operations["disconnect"].block()
    valve.operations["dashboard"].failure = OSError("session lost")
    await clock.run(gate.started.wait())
    unload = asyncio.create_task(connection.async_unload())
    try:
        await clock.drain()
        assert not unload.done()
        assert valve.clients[0].is_connected
    finally:
        gate.release.set()
        # Cancellation during normal teardown once surfaced a RuntimeError
        # instead of completing unload. Assert the public unload result.
        assert await clock.run(unload) is None
    assert_closed(valve)


async def test_concurrent_pending_stops_share_cleanup_after_cancellation(connection_factory, transport, clock):
    connection = connection_factory()
    await connection.async_set_persistent_connection_enabled(True)
    valve = transport.valve()
    gate = valve.operations["disconnect"].block()
    disable = asyncio.create_task(connection.async_set_persistent_connection_enabled(False))
    unload = asyncio.create_task(connection.async_unload())
    await connection.async_poll()
    try:
        await clock.run(gate.started.wait())
        disable.cancel()
        await clock.drain()
        assert not disable.done() and not unload.done()
        assert valve.clients[0].is_connected
    finally:
        gate.release.set()
        results = await clock.run(asyncio.gather(disable, unload, return_exceptions=True))
    assert isinstance(results[0], asyncio.CancelledError)
    assert results[1] is None
    assert_closed(valve)


async def test_reset_timeout_preserves_disconnect_budget(connection_factory, transport, clock, caplog):
    connection = connection_factory()
    valve = transport.valve()
    reset = valve.operations["reset"].block()
    disconnect = valve.operations["disconnect"].block()
    poll = asyncio.create_task(connection.async_poll())
    try:
        await clock.run(disconnect.started.wait())
        assert reset.cancelled
        assert not poll.done()
        # Even after RESET exhausts its five-second budget, disconnect gets
        # its own full thirty-second budget rather than sharing the remainder.
        await clock.advance(29)
        assert not poll.done()
        assert not disconnect.cancelled
    finally:
        disconnect.release.set()
        reset.release.set()
        await clock.run(poll)
    assert "Timed out resetting valve valve-a" in caplog.text
    assert_closed(valve)


async def test_disconnect_timeout_reports_possible_open_connection(connection_factory, transport, clock, caplog):
    events = []
    connection = connection_factory(state_callback=lambda address, connected: events.append(connected))
    valve = transport.valve()
    gate = valve.operations["disconnect"].block()
    try:
        await clock.run(connection.async_poll())
        assert gate.cancelled
        # A completed timeout is not evidence that the external connection
        # closed. The simulated boundary intentionally remains connected.
        assert valve.clients[0].is_connected
        assert valve.clients[0].disconnects == 1
        assert events == [True, False]
        assert "Bluetooth connection may still be open" in caplog.text
    finally:
        gate.release.set()
        await valve.clients[0].disconnect()


async def test_reset_failure_still_disconnects(connection_factory, transport, clock):
    connection = connection_factory()
    transport.valve().operations["reset"].failure = OSError("proxy unavailable during reset")
    await clock.run(connection.async_poll())
    assert_closed(transport.valve())


async def test_cancelled_unload_waits_for_active_poll(connection_factory, transport, clock):
    connection = connection_factory()
    valve = transport.valve()
    gate = valve.operations["disconnect"].block()
    poll = asyncio.create_task(connection.async_poll())
    unload = None
    try:
        await clock.run(gate.started.wait())
        unload = asyncio.create_task(connection.async_unload())
        await clock.drain()
        unload.cancel()
        await clock.drain()
        assert not unload.done()
        assert valve.clients[0].is_connected
    finally:
        gate.release.set()
        tasks = [poll] if unload is None else [poll, unload]
        results = await clock.run(asyncio.gather(*tasks, return_exceptions=True))
    assert results[0] is None
    assert isinstance(results[1], asyncio.CancelledError)
    assert_closed(valve)


async def test_cancelled_manager_shutdown_drains_real_connections(hass, entry_factory, discovery_manager, bluetooth_io, transport, clock):
    manager = ValveConnectionManager(hass, entry_factory(), discovery_manager)
    await manager.async_setup()
    bluetooth_io.advertise()
    connection = manager.get_connection("valve-a")
    await connection.async_set_persistent_connection_enabled(True)
    await connection.async_poll()
    valve = transport.valve()
    gate = valve.operations["disconnect"].block()
    shutdown = asyncio.create_task(manager.async_shutdown())
    try:
        await clock.run(gate.started.wait())
        shutdown.cancel()
        await clock.drain()
        assert not shutdown.done()
        assert manager.get_connection("valve-a") is connection
        assert valve.clients[0].is_connected
    finally:
        gate.release.set()
        result = await clock.run(asyncio.gather(shutdown, return_exceptions=True))
        await manager.async_unload()
    assert isinstance(result[0], asyncio.CancelledError)
    assert list(manager.get_connections()) == []
    assert_closed(valve)
