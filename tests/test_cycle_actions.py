"""Exercise one-shot cycle buttons through HA and a simulated valve."""

import asyncio

import pytest

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from custom_components.chandler_legacy_view.const import DOMAIN
from evb034_packets import frames
from test_cycle_status import cycle_dashboard, cycle_state, setup_cycle_valve


def cycle_reply(firmware, position, *, active=True):
    """Return an independent Dashboard observation for the selected peer."""
    if firmware >= 600:
        return frames({
            "gra": int(active), "grs": 5 if active else 0, "drcp": position,
            "drtr": 4 if active else 0, "drtt": 1,
        })
    return cycle_dashboard(position, active=int(active), timer=4 if active else 0)


def button_id(hass, suffix):
    """Return an action button's registered Home Assistant ID."""
    return er.async_get(hass).async_get_entity_id("button", DOMAIN, f"valve-a_{suffix}")


async def press(hass, suffix):
    """Invoke the regular Home Assistant button service and await its outcome."""
    await hass.services.async_call(
        "button", "press", {"entity_id": button_id(hass, suffix)}, blocking=True,
    )


@pytest.mark.parametrize(("firmware", "now", "later"), [
    (422, b"RN", b"RT"),
    (622, b'{"grn":0}', b'{"grl":0}'),
])
async def test_filter_commissioning_starts_and_advances_then_rinse_finishes_on_its_own(
    hass, runtime_factory, bluetooth_io, transport, clock, firmware, now, later,
):
    connection, valve = await setup_cycle_valve(
        hass, runtime_factory, bluetooth_io, transport, firmware=firmware,
    )
    if firmware >= 600:
        await connection.async_set_persistent_connection_enabled(True)
    idle = cycle_reply(firmware, 0, active=False)
    valve.dashboards.append(idle)
    await clock.run(connection.async_refresh())
    await hass.async_block_till_done()
    start = button_id(hass, "regenerate_now")
    advance = button_id(hass, "advance_cycle")
    schedule = button_id(hass, "regenerate_at_next_scheduled_time")
    assert hass.states.get(start).name.endswith("Backwash now")
    assert hass.states.get(advance).state == "unavailable"
    assert hass.states.get(schedule).name.endswith("Backwash at next scheduled time")
    assert hass.states.get(schedule).state != "unavailable"

    valve.dashboards.append(idle)
    valve.cycle_replies.append(cycle_reply(firmware, 1))
    await clock.run(press(hass, "regenerate_now"))
    assert valve.cycle_writes == [now]
    assert cycle_state(hass, "sensor", "current_cycle").state == "backwash"
    assert hass.states.get(start).state == hass.states.get(schedule).state == "unavailable"

    rest = cycle_reply(firmware, 2)
    valve.dashboards.append(rest)
    await clock.run(connection.async_refresh())
    assert cycle_state(hass, "sensor", "current_cycle").state == "rest"
    valve.dashboards.append(rest)
    valve.cycle_replies.append(cycle_reply(firmware, 3))
    await clock.run(press(hass, "advance_cycle"))
    assert cycle_state(hass, "sensor", "current_cycle").state == "rapid_rinse"
    assert valve.cycle_writes == [now, now]

    valve.dashboards.append(idle)
    await clock.run(connection.async_refresh())
    assert cycle_state(hass, "binary_sensor", "regeneration_active").state == "off"
    assert valve.cycle_writes == [now, now]

    valve.dashboards.append(idle)
    await clock.run(press(hass, "regenerate_at_next_scheduled_time"))
    assert valve.cycle_writes == [now, now, later]
    assert cycle_state(hass, "binary_sensor", "regeneration_active").state == "off"


@pytest.mark.parametrize(("button", "before", "fresh"), [
    ("regenerate_now", 0, 1),
    ("advance_cycle", 2, 0),
    ("advance_cycle", 2, 3),
    ("regenerate_at_next_scheduled_time", 0, 1),
])
async def test_cycle_action_rejects_a_changed_phase_before_sending(
    hass, runtime_factory, bluetooth_io, transport, clock, button, before, fresh,
):
    connection, valve = await setup_cycle_valve(hass, runtime_factory, bluetooth_io, transport)
    valve.dashboards.append(cycle_reply(422, before, active=bool(before)))
    await clock.run(connection.async_refresh())
    assert hass.states.get(button_id(hass, button)).state != "unavailable"
    valve.dashboards.append(cycle_reply(422, fresh, active=bool(fresh)))
    with pytest.raises(HomeAssistantError, match="Fresh valve state"):
        await clock.run(press(hass, button))
    assert valve.cycle_writes == []


async def test_twin_offers_the_regenerate_buttons_without_the_scheduled_action(
    hass, runtime_factory, bluetooth_io, transport, clock,
):
    connection, valve = await setup_cycle_valve(
        hass, runtime_factory, bluetooth_io, transport,
        valve_type=1, firmware=122, twin=True,
    )
    valve.dashboards.append(cycle_reply(122, 0, active=False))
    await clock.run(connection.async_refresh())
    assert hass.states.get(button_id(hass, "regenerate_now")).name.endswith("Regenerate now")
    assert button_id(hass, "advance_cycle") is not None
    assert button_id(hass, "regenerate_at_next_scheduled_time") is None


async def test_supported_brine_soak_allows_advance_even_without_active_cycle_flag(
    hass, runtime_factory, bluetooth_io, transport, clock,
):
    connection, valve = await setup_cycle_valve(
        hass, runtime_factory, bluetooth_io, transport, valve_type=1,
    )
    soak = cycle_dashboard(0, active=0, soak=4)
    valve.dashboards.extend([soak, soak])
    await clock.run(connection.async_refresh())
    assert cycle_state(hass, "sensor", "current_cycle").state == "brine_soak"
    assert hass.states.get(button_id(hass, "regenerate_now")).state == "unavailable"
    valve.cycle_replies.append(cycle_reply(422, 1))
    await clock.run(press(hass, "advance_cycle"))
    assert cycle_state(hass, "sensor", "current_cycle").state == "backwash"
    assert valve.cycle_writes == [b"RN"]


@pytest.mark.parametrize("ack", [None, b"\xc8", b"\xc2"])
async def test_evb034_missing_or_negative_ack_does_not_replay_cycle_frame(
    hass, runtime_factory, bluetooth_io, transport, clock, ack,
):
    connection, valve = await setup_cycle_valve(
        hass, runtime_factory, bluetooth_io, transport, firmware=622,
    )
    idle = cycle_reply(622, 0, active=False)
    valve.dashboards.append(idle)
    await clock.run(connection.async_refresh())
    await hass.async_block_till_done()
    valve.dashboards.append(idle)
    valve.chunk_acknowledgements.append(ack)
    with pytest.raises(HomeAssistantError, match="acknowledge|unconfirmed"):
        await clock.run(press(hass, "regenerate_now"))
    [wire_command] = frames({"grn": 0})
    assert sum(payload == wire_command for _, payload in valve.writes) == 1
    assert valve.cycle_writes == []


async def test_evb019_missing_transition_is_unconfirmed_and_recovery_does_not_resend(
    hass, runtime_factory, bluetooth_io, transport, clock,
):
    connection, valve = await setup_cycle_valve(hass, runtime_factory, bluetooth_io, transport)
    idle = cycle_reply(422, 0, active=False)
    valve.dashboards.extend([idle, idle])
    await clock.run(connection.async_refresh())
    with pytest.raises(HomeAssistantError, match="unconfirmed"):
        await clock.run(press(hass, "regenerate_now"))
    assert cycle_state(hass, "binary_sensor", "regeneration_active").state == "unavailable"
    valve.dashboards.append(cycle_reply(422, 1))
    await clock.run(connection.async_refresh())
    assert cycle_state(hass, "sensor", "current_cycle").state == "backwash"
    assert valve.cycle_writes == [b"RN"]


async def test_evb034_transport_ack_and_unrelated_readings_do_not_confirm_start(
    hass, runtime_factory, bluetooth_io, transport, clock,
):
    connection, valve = await setup_cycle_valve(
        hass, runtime_factory, bluetooth_io, transport, firmware=622,
    )
    idle = cycle_reply(622, 0, active=False)
    valve.dashboards.extend([idle, idle])
    await clock.run(connection.async_refresh())
    await hass.async_block_till_done()
    # The peer ACKs the command, but gives no new evidence of a cycle start.
    valve.cycle_replies.append(frames({"gpf": 400}))
    with pytest.raises(HomeAssistantError, match="unconfirmed"):
        await clock.run(press(hass, "regenerate_now"))
    assert valve.cycle_writes == [b'{"grn":0}']
    assert cycle_state(hass, "binary_sensor", "regeneration_active").state == "unavailable"


@pytest.mark.parametrize("button", ["regenerate_now", "regenerate_at_next_scheduled_time"])
async def test_evb034_idle_action_waits_for_fresh_soak_status(
    hass, runtime_factory, bluetooth_io, transport, clock, button,
):
    connection, valve = await setup_cycle_valve(
        hass, runtime_factory, bluetooth_io, transport, firmware=622, valve_type=1,
    )
    await connection.async_set_persistent_connection_enabled(True)
    idle_fields = {"gra": 0, "grs": 0, "drcp": 0}
    valve.dashboards.append(frames({**idle_fields, "dps": 0}))
    await clock.run(connection.async_refresh())
    await hass.async_block_till_done()
    assert hass.states.get(button_id(hass, button)).state != "unavailable"

    # The new write session receives the soak flag after the other fields.
    # The Now command would advance this soak instead of starting a cycle.
    valve.dashboards.extend([frames(idle_fields), frames({"dps": 1, "drst": 5})])
    with pytest.raises(HomeAssistantError, match="Fresh valve state"):
        await clock.run(press(hass, button))
    assert valve.cycle_writes == []


async def test_evb034_start_proceeds_after_split_update_confirms_soak_is_inactive(
    hass, runtime_factory, bluetooth_io, transport, clock,
):
    connection, valve = await setup_cycle_valve(
        hass, runtime_factory, bluetooth_io, transport, firmware=622, valve_type=1,
    )
    await connection.async_set_persistent_connection_enabled(True)
    idle_fields = {"gra": 0, "grs": 0, "drcp": 0}
    valve.dashboards.append(frames({**idle_fields, "dps": 0}))
    await clock.run(connection.async_refresh())
    await hass.async_block_till_done()

    valve.dashboards.extend([frames(idle_fields), frames({"dps": 0})])
    valve.cycle_replies.append(cycle_reply(622, 1))
    await clock.run(press(hass, "regenerate_now"))
    assert valve.cycle_writes == [b'{"grn":0}']
    assert cycle_state(hass, "sensor", "current_cycle").state == "backwash"


async def test_cancelling_a_sent_action_and_refreshing_does_not_repeat_it(
    hass, runtime_factory, bluetooth_io, transport, clock,
):
    connection, valve = await setup_cycle_valve(hass, runtime_factory, bluetooth_io, transport)
    idle = cycle_reply(422, 0, active=False)
    valve.dashboards.extend([idle, idle])
    await clock.run(connection.async_refresh())
    gate = valve.operations["cycle_action"].block()
    pressing = asyncio.create_task(press(hass, "regenerate_now"))
    await clock.run(gate.started.wait())
    pressing.cancel()
    gate.release.set()
    with pytest.raises(asyncio.CancelledError):
        await clock.run(pressing)
    valve.dashboards.append(cycle_reply(422, 1))
    await clock.run(connection.async_refresh())
    assert cycle_state(hass, "sensor", "current_cycle").state == "backwash"
    assert valve.cycle_writes == [b"RN"]
