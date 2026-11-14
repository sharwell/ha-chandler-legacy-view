"""Explicit configuration changes share connections without bypassing ownership."""

import asyncio

import pytest
from homeassistant.exceptions import HomeAssistantError

from test_configured_authentication import entity_id
from test_display_off import setup_display, write_display


@pytest.mark.parametrize("firmware", [440, 618])
@pytest.mark.parametrize("persistent", [False, True])
async def test_explicit_display_changes_do_not_wait_for_connection_cooldown(
    hass, runtime_factory, bluetooth_io, transport, clock, firmware, persistent,
):
    _, connection, valve = await setup_display(
        hass, runtime_factory, bluetooth_io, transport, clock, firmware,
    )
    if persistent:
        await connection.async_set_persistent_connection_enabled(True)
        await clock.run(connection.async_poll())
    for enabled in (True, False):
        started = clock.loop.time()
        await clock.run(write_display(hass, enabled))
        # Simulated GATT replies immediately. A thirty-second pause here is
        # connection pacing, not time spent waiting for valve confirmation.
        assert clock.loop.time() - started < 5
        assert hass.states.get(entity_id(hass, "switch", "valve-a", "display_off")).state == (
            "on" if enabled else "off"
        )
    assert [value for _, value in valve.display_writes] == [1, 0]
    assert valve.clients[-1].is_connected is persistent


@pytest.mark.parametrize("firmware", [440, 618])
async def test_ordinary_refresh_still_observes_cooldown_after_a_configuration_write(
    hass, runtime_factory, bluetooth_io, transport, clock, firmware,
):
    _, connection, _ = await setup_display(
        hass, runtime_factory, bluetooth_io, transport, clock, firmware,
    )
    await clock.run(write_display(hass, True))
    attempts = len(transport.attempts)
    refreshing = asyncio.create_task(connection.async_refresh())
    await clock.advance(1)
    assert not refreshing.done()
    assert len(transport.attempts) == attempts
    await clock.run(refreshing)
    assert len(transport.attempts) == attempts + 1


@pytest.mark.parametrize("firmware", [440, 618])
@pytest.mark.parametrize("enabled", [False, True])
async def test_changes_queued_during_connection_send_only_the_latest_value(
    hass, runtime_factory, bluetooth_io, transport, clock, firmware, enabled,
):
    _, _, valve = await setup_display(
        hass, runtime_factory, bluetooth_io, transport, clock, firmware,
    )
    attempts = len(transport.attempts)
    gate = valve.operations["connect"].block()
    gate.started.clear()
    first = asyncio.create_task(write_display(hass, enabled))
    await clock.run(gate.started.wait())
    second = asyncio.create_task(write_display(hass, not enabled))
    latest = asyncio.create_task(write_display(hass, enabled))
    await clock.drain()
    assert valve.display_writes == []
    gate.release.set()
    await clock.run(asyncio.gather(first, second, latest))
    assert len(transport.attempts) == attempts + 1
    assert [value for _, value in valve.display_writes] == [int(enabled)]
    assert hass.states.get(entity_id(hass, "switch", "valve-a", "display_off")).state == (
        "on" if enabled else "off"
    )


@pytest.mark.parametrize("firmware", [440, 618])
async def test_inflight_change_is_confirmed_before_the_latest_pending_change(
    hass, runtime_factory, bluetooth_io, transport, clock, firmware,
):
    _, _, valve = await setup_display(
        hass, runtime_factory, bluetooth_io, transport, clock, firmware,
    )
    attempts = len(transport.attempts)
    gate = valve.operations["display_setting"].block()
    first = asyncio.create_task(write_display(hass, True))
    await clock.run(gate.started.wait())
    pending = [asyncio.create_task(write_display(hass, value)) for value in (False, True, False)]
    await clock.drain()
    assert len(valve.display_writes) == 1
    assert hass.states.get(entity_id(hass, "switch", "valve-a", "display_off")).state == "off"
    gate.release.set()
    await clock.run(asyncio.gather(first, *pending))
    assert [value for _, value in valve.display_writes] == [1, 0]
    assert len({client for client, _ in valve.display_writes}) == 1
    assert len(transport.attempts) == attempts + 1
    assert hass.states.get(entity_id(hass, "switch", "valve-a", "display_off")).state == "off"


@pytest.mark.parametrize("firmware", [440, 618])
async def test_canceling_the_latest_unsent_change_restores_the_previous_desired_value(
    hass, runtime_factory, bluetooth_io, transport, clock, firmware,
):
    _, _, valve = await setup_display(
        hass, runtime_factory, bluetooth_io, transport, clock, firmware,
    )
    gate = valve.operations["connect"].block()
    gate.started.clear()
    first = asyncio.create_task(write_display(hass, True))
    await clock.run(gate.started.wait())
    latest = asyncio.create_task(write_display(hass, False))
    await clock.drain()
    latest.cancel()
    with pytest.raises(asyncio.CancelledError):
        await latest
    assert not gate.cancelled
    gate.release.set()
    await clock.run(first)
    assert [value for _, value in valve.display_writes] == [1]


@pytest.mark.parametrize("firmware", [440, 618])
async def test_canceling_an_inflight_caller_preserves_other_pending_requests(
    hass, runtime_factory, bluetooth_io, transport, clock, firmware,
):
    _, _, valve = await setup_display(
        hass, runtime_factory, bluetooth_io, transport, clock, firmware,
    )
    gate = valve.operations["display_setting"].block()
    first = asyncio.create_task(write_display(hass, True))
    await clock.run(gate.started.wait())
    latest = asyncio.create_task(write_display(hass, False))
    await clock.drain()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert not gate.cancelled
    gate.release.set()
    await clock.run(latest)
    assert [value for _, value in valve.display_writes] == [1, 0]
    assert len({client for client, _ in valve.display_writes}) == 1


@pytest.mark.parametrize("firmware", [440, 618])
async def test_failed_batch_connection_fails_callers_without_retrying_their_requests(
    hass, runtime_factory, bluetooth_io, transport, clock, firmware,
):
    _, connection, valve = await setup_display(
        hass, runtime_factory, bluetooth_io, transport, clock, firmware,
    )
    attempts = len(transport.attempts)
    gate = valve.operations["connect"].block()
    gate.started.clear()
    gate.failure = OSError("Adapter connection failed")
    first = asyncio.create_task(write_display(hass, True))
    await clock.run(gate.started.wait())
    latest = asyncio.create_task(write_display(hass, False))
    await clock.drain()
    gate.release.set()
    results = await clock.run(asyncio.gather(first, latest, return_exceptions=True))
    assert all(isinstance(result, HomeAssistantError) for result in results)
    assert len(transport.attempts) == attempts + 1
    assert valve.display_writes == []
    gate.failure = None
    await clock.run(connection.async_set_display_off(True))
    assert len(transport.attempts) == attempts + 2
    assert [value for _, value in valve.display_writes] == [1]


@pytest.mark.parametrize("firmware", [440, 618])
async def test_a_later_rejection_does_not_fail_a_previously_confirmed_change(
    hass, runtime_factory, bluetooth_io, transport, clock, firmware,
):
    _, _, valve = await setup_display(
        hass, runtime_factory, bluetooth_io, transport, clock, firmware,
    )
    valve.display_setting_acceptance.extend([True, False])
    gate = valve.operations["display_setting"].block()
    first = asyncio.create_task(write_display(hass, True))
    await clock.run(gate.started.wait())
    latest = asyncio.create_task(write_display(hass, False))
    await clock.drain()
    gate.release.set()
    first_result, latest_result = await clock.run(asyncio.gather(first, latest, return_exceptions=True))
    assert first_result is None
    assert isinstance(latest_result, HomeAssistantError)
    assert [value for _, value in valve.display_writes] == [1, 0]
    assert len({client for client, _ in valve.display_writes}) == 1
    assert not valve.clients[-1].is_connected


@pytest.mark.parametrize("firmware", [440, 618])
async def test_a_change_received_during_cleanup_starts_only_after_release(
    hass, runtime_factory, bluetooth_io, transport, clock, firmware,
):
    _, _, valve = await setup_display(
        hass, runtime_factory, bluetooth_io, transport, clock, firmware,
    )
    gate = valve.operations["disconnect"].block()
    gate.started.clear()
    first = asyncio.create_task(write_display(hass, True))
    await clock.run(gate.started.wait())
    source = valve.clients[-1]
    attempts = len(transport.attempts)
    latest = asyncio.create_task(write_display(hass, False))
    await clock.drain()
    assert len(transport.attempts) == attempts and source.is_connected
    assert len(valve.display_writes) == 1
    gate.release.set()
    await clock.run(asyncio.gather(first, latest))
    assert len(transport.attempts) == attempts + 1
    assert not source.is_connected
    assert [value for _, value in valve.display_writes] == [1, 0]
    assert len({client for client, _ in valve.display_writes}) == 2


@pytest.mark.parametrize("firmware", [440, 618])
async def test_coalesced_callers_all_receive_an_unconfirmed_write_error(
    hass, runtime_factory, bluetooth_io, transport, clock, firmware,
):
    _, _, valve = await setup_display(
        hass, runtime_factory, bluetooth_io, transport, clock, firmware,
    )
    gate = valve.operations["connect"].block()
    gate.started.clear()
    first = asyncio.create_task(write_display(hass, True))
    await clock.run(gate.started.wait())
    pending = [asyncio.create_task(write_display(hass, value)) for value in (False, True)]
    await clock.drain()
    valve.report_display_off = False
    gate.release.set()
    results = await clock.run(asyncio.gather(first, *pending, return_exceptions=True))
    assert all(isinstance(result, HomeAssistantError) for result in results)
    assert [value for _, value in valve.display_writes] == [1]
    assert not valve.clients[-1].is_connected


@pytest.mark.parametrize("firmware", [440, 618])
async def test_unload_cancels_the_inflight_and_all_unsent_callers(
    hass, runtime_factory, bluetooth_io, transport, clock, firmware,
):
    entry, connection, valve = await setup_display(
        hass, runtime_factory, bluetooth_io, transport, clock, firmware,
    )
    gate = valve.operations["display_setting"].block()
    first = asyncio.create_task(write_display(hass, True))
    await clock.run(gate.started.wait())
    pending = [asyncio.create_task(write_display(hass, value)) for value in (False, True)]
    await clock.drain()
    assert await clock.run(hass.config_entries.async_unload(entry.entry_id))
    results = await asyncio.gather(first, *pending, return_exceptions=True)
    assert all(isinstance(result, asyncio.CancelledError) for result in results)
    assert not connection.cleanup_pending
    assert gate.cancelled
    assert all(not client.is_connected for client in valve.clients)
    assert len(valve.display_writes) == 1


@pytest.mark.parametrize("firmware", [440, 618])
async def test_unload_cancels_a_batch_before_its_worker_starts(
    hass, runtime_factory, bluetooth_io, transport, clock, firmware,
):
    _, connection, valve = await setup_display(
        hass, runtime_factory, bluetooth_io, transport, clock, firmware,
    )
    attempts = len(transport.attempts)
    writing = asyncio.create_task(connection.async_set_display_off(True))
    unloading = asyncio.create_task(connection.async_unload())
    result, unloaded = await clock.run(asyncio.gather(writing, unloading, return_exceptions=True))
    assert isinstance(result, asyncio.CancelledError)
    assert unloaded is None
    assert not connection.cleanup_pending
    assert len(transport.attempts) == attempts
    assert valve.display_writes == []
