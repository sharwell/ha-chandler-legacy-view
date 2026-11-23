"""Publish cycle positions and arrival from simulated, authenticated valve reads."""

import pytest

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er

from custom_components.chandler_legacy_view.const import DATA_CONNECTION_MANAGER, DOMAIN
from evb034_packets import frames
from packets import EVB034_622, MODERN_422, TWIN_122, dashboard_frames


def cycle_state(hass, platform, suffix):
    """Return the registered cycle entity state, or None if unsupported."""
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"valve-a_{suffix}")
    return hass.states.get(entity_id) if entity_id else None


def cycle_dashboard(position, *, active=1, timer=3, seconds=0, soak=0, tank=0):
    """Build an independent Evb019 wire fixture with a selected cycle observation."""
    dashboard = dashboard_frames()
    packet = bytearray(dashboard[1])
    packet[5:9] = bytes((timer, seconds, position, active))
    packet[10:12] = bytes((8 if soak else 0, soak))
    packet[18] = tank
    dashboard[1] = bytes(packet)
    return dashboard


async def setup_cycle_valve(hass, runtime_factory, bluetooth_io, transport,
                            *, valve_type=4, firmware=422, twin=False, clack=False):
    """Set up a real HA integration with an independently identified test peer."""
    entry = await runtime_factory()
    valve = transport.valve()
    valve.firmware_version = firmware
    valve.is_twin_valve = twin
    if firmware >= 600:
        bluetooth_io.advertise(payload=EVB034_622)
        valve.identification_packets.append(frames({
            "dlf": firmware, "dlc": 23, "as": 1, "dlvt": valve_type,
            "dli": int(twin), "dlic": int(clack), "gbre": 1,
        }))
        valve.is_clack_valve = clack
    else:
        payload = bytearray(TWIN_122 if twin else MODERN_422)
        payload[11] = valve_type
        payload[-2:] = bytes((firmware // 100, int(f"{firmware % 100:02}", 16)))
        bluetooth_io.advertise(manufacturer_data={1850: bytes(payload[2:])})
    await hass.async_block_till_done()
    manager = hass.data[DOMAIN][entry.entry_id][DATA_CONNECTION_MANAGER]
    return manager.get_connection("valve-a"), valve


async def test_evb019_filter_publishes_movement_arrival_next_step_and_completion(
    hass, runtime_factory, bluetooth_io, transport, clock,
):
    connection, valve = await setup_cycle_valve(hass, runtime_factory, bluetooth_io, transport)
    step = lambda: cycle_state(hass, "sensor", "current_cycle")
    movement = lambda: cycle_state(hass, "binary_sensor", "valve_moving")
    duration = lambda: cycle_state(hass, "sensor", "cycle_time_remaining")
    assert cycle_state(hass, "binary_sensor", "brine_soak_active") is None
    assert cycle_state(hass, "binary_sensor", "aeration_active") is None
    states = []

    @callback
    def observe(event):
        if event.data["entity_id"] == step().entity_id:
            states.append(event.data["new_state"].state)

    remove = hass.bus.async_listen(EVENT_STATE_CHANGED, observe)
    try:
        for position, active, timer, expected, remaining in (
            (1, 1, 101, "unavailable", "unavailable"),
            (1, 1, 3, "backwash", "180"),
            (2, 1, 5, "rest", "300"),
            (3, 1, 2, "rapid_rinse", "120"),
            (0, 0, 0, "service", "0"),
        ):
            valve.dashboards.append(cycle_dashboard(position, active=active, timer=timer))
            await clock.run(connection.async_refresh())
            assert step().state == expected
            assert movement().state == ("on" if timer > 100 else "off")
            assert movement().attributes.get("moving_to") == ("backwash" if timer > 100 else None)
            assert duration().state == remaining
            assert duration().attributes.get("unit_of_measurement") == "s"
            assert cycle_state(hass, "binary_sensor", "regeneration_active").state == (
                "on" if active else "off"
            )
    finally:
        remove()
    assert [state for state in states if state != "unavailable"] == [
        "backwash", "rest", "rapid_rinse", "service",
    ]
    assert step().attributes["options"] == ["service", "backwash", "rest", "rapid_rinse"]


@pytest.mark.parametrize(("kind", "firmware", "twin", "position", "timer", "soak", "tank", "expected"), [
    (17, 422, False, 3, 45, 0, 0, ("second_backwash", "45", "off")),
    (14, 422, False, 8, 45, 0, 0, ("rapid_rinse", "45", None)),
    (10, 437, False, 1, 45, 0, 0, ("decompress", "45", None)),
    (1, 422, False, 0, 4, 7, 0, ("brine_soak", "420", "on")),
    (1, 122, True, 1, 4, 0, 129, ("unavailable", "unavailable", "off")),
])
async def test_evb019_cycle_names_use_the_valves_program(
    hass, runtime_factory, bluetooth_io, transport, clock,
    kind, firmware, twin, position, timer, soak, tank, expected,
):
    connection, valve = await setup_cycle_valve(
        hass, runtime_factory, bluetooth_io, transport,
        valve_type=kind, firmware=firmware, twin=twin,
    )
    valve.dashboards.append(cycle_dashboard(position, timer=timer, seconds=1, soak=soak, tank=tank))
    await clock.run(connection.async_refresh())
    await hass.async_block_till_done()
    soaking = cycle_state(hass, "binary_sensor", "brine_soak_active")
    assert (cycle_state(hass, "sensor", "current_cycle").state,
            cycle_state(hass, "sensor", "cycle_time_remaining").state,
            soaking.state if soaking else None) == expected
    aeration = cycle_state(hass, "binary_sensor", "aeration_active")
    assert (aeration.state if aeration else None) == ("on" if kind == 14 else None)


async def test_evb034_partial_cycle_updates_cannot_present_previous_step_as_arrival(
    hass, runtime_factory, bluetooth_io, transport, clock,
):
    connection, valve = await setup_cycle_valve(
        hass, runtime_factory, bluetooth_io, transport, firmware=622,
    )
    await connection.async_set_persistent_connection_enabled(True)
    await connection.async_set_persistent_poll_interval(300)
    valve.dashboards.append(frames(
        {"gra": 1, "grs": 5, "drcp": 1, "drtr": 3, "drtt": 1, "gpf": 100},
    ))
    await clock.run(connection.async_refresh())
    await hass.async_block_till_done()
    step = lambda: cycle_state(hass, "sensor", "current_cycle")
    movement = lambda: cycle_state(hass, "binary_sensor", "valve_moving")
    remaining = lambda: cycle_state(hass, "sensor", "cycle_time_remaining")
    assert (step().state, remaining().state) == ("backwash", "180")
    # A notification can begin moving before its destination arrives. Backwash
    # must cease to look confirmed without turning it into the new target.
    valve.dashboards.append(frames({"grs": 1, "gpf": 200}))
    await clock.until(lambda: movement().state == "on")
    assert step().state == "unavailable"
    assert "moving_to" not in movement().attributes
    assert float(cycle_state(hass, "sensor", "present_flow").state) == 1.0
    valve.dashboards.append(frames({"drcp": 2}))
    await clock.until(lambda: movement().attributes.get("moving_to") == "rest")
    valve.dashboards.append(frames({"grs": 5}))
    await clock.until(lambda: step().state == "rest")
    assert remaining().state == "unavailable"
    valve.dashboards.append(frames({"drtr": 27, "drtt": 2}))
    await clock.until(lambda: remaining().state == "27")
    valve.dashboards.append(frames({"drcp": None, "gra": 3, "grs": 91}))
    await clock.until(lambda: step().state == "unavailable")
    assert movement().state == "unavailable"
    assert cycle_state(hass, "binary_sensor", "regeneration_active").state == "unavailable"
    valve.dashboards.append(frames({"gra": 1, "grs": 5, "drcp": 3, "drtr": 9, "drtt": 0}))
    await clock.until(lambda: step().state == "rapid_rinse")
    assert remaining().state == "9"


async def test_clack_filter_uses_live_dashboard_step_names(
    hass, runtime_factory, bluetooth_io, transport, clock,
):
    connection, valve = await setup_cycle_valve(
        hass, runtime_factory, bluetooth_io, transport,
        firmware=622, valve_type=2, clack=True,
    )
    await connection.async_set_persistent_connection_enabled(True)
    # The live Dashboard names this position Air Draw even when the timing
    # screen calls it Rest. The position alone cannot imply aeration support.
    valve.dashboards.append(frames({"gra": 1, "grs": 5, "drcp": 2, "drtr": 2, "drtt": 1, "dria": 1}))
    await clock.run(connection.async_refresh())
    await hass.async_block_till_done()
    step = cycle_state(hass, "sensor", "current_cycle")
    assert step.state == "air_draw"
    assert step.attributes["options"] == ["service", "backwash", "air_draw", "rapid_rinse"]
    assert cycle_state(hass, "binary_sensor", "aeration_active") is None
