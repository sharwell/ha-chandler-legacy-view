"""Interpret authenticated valve cycle readings from both BLE protocols."""

from .models import ValveCapabilities, ValveCycleStatus, ValveDashboardData

CYCLE_READING = "cycle_status"
EVB034_MOVING = frozenset({1, 2, 6, 7, 9, 11})
_EVB034_STATIONARY = frozenset({0, 5, 8, 10})

_SOFTENER = ("service", "backwash", "brine_draw", "rapid_rinse", "brine_fill")
_SALT_DOSE = ("service", "backwash", "brine_draw", "second_backwash", "rapid_rinse", "brine_fill")
_FILTER = ("service", "backwash", "rest", "rapid_rinse")
_SIDEKICK = ("service", "air_release", "backwash", "rest", "air_draw", "rapid_rinse")
_DECOMPRESSION = ("service", "decompress", *_SIDEKICK[1:])
_NITRO_PRO = ("service", "air_release", "air_chlorine_draw", "chlorine_rinse",
              "chlorine_contact", "backwash", "rest", "rapid_rinse", "air_draw")
_PROGRAMS = {
    **dict.fromkeys((1, 2, 3), _SOFTENER),
    **dict.fromkeys((4, 5, 6, 7, 18, 20, 22, 26, 27), _FILTER),
    8: ("service", "chlorine_feed", "rest", "backwash"),
    **dict.fromkeys((9, 11), ("service", "air_release", "backwash", "rest", "rapid_rinse", "air_draw")),
    **dict.fromkeys((10, 12, 16), _SIDEKICK),
    13: _NITRO_PRO,
    **dict.fromkeys((14, 15), (*_NITRO_PRO[:-2], "air_draw", "rapid_rinse")),
    **dict.fromkeys((17, 19, 21), _SALT_DOSE),
    **dict.fromkeys((23, 24, 25), _DECOMPRESSION),
}


def cycle_program(capabilities: ValveCapabilities) -> tuple[str, ...]:
    """Return the Dashboard position names in wire order, or empty if unknown.

    Index zero is the program's Service or Standby position. Shutoff and
    brine soak are reported separately from the modeled sequence.
    """
    kind = capabilities.valve_type_full
    if capabilities.is_clack_valve:
        if capabilities.is_twin_valve or kind in (1, 4, 6, 8):
            return _SALT_DOSE
        if kind in (2, 3, 5, 7, 9):
            # The app's live position mapping calls Clack position 2 Air Draw.
            return ("service", "backwash", "air_draw", "rapid_rinse")
        return ()
    if capabilities.is_twin_valve:
        return ("standby", *_SALT_DOSE[1:])
    firmware = capabilities.firmware_version
    if kind in (10, 12, 16) and firmware is not None and (
        437 <= firmware < 500 or 610 <= firmware < 700
    ):
        return _DECOMPRESSION
    return _PROGRAMS.get(kind, ())


def supports_evb019_soak(capabilities: ValveCapabilities) -> bool:
    """Return whether Evb019 decodes the prefill-soak bytes for this valve."""
    return supports_soak(capabilities) and bool(
        capabilities.is_twin_valve or (capabilities.firmware_version or 0) >= 210
    )


def supports_soak(capabilities: ValveCapabilities) -> bool:
    """Return whether the reported program has a brine phase."""
    return "brine_draw" in cycle_program(capabilities)


def supports_aeration(capabilities: ValveCapabilities, *, evb034: bool) -> bool:
    """Return whether Legacy View exposes aeration state for this combination."""
    if not evb034:
        return capabilities.valve_type == "NitroProSidekick"
    if capabilities.is_twin_valve:
        return False
    if capabilities.is_clack_valve:
        return capabilities.valve_type_full == 3
    return capabilities.valve_type_full in (9, 10, 11, 12, 13, 14, 15, 16, 23, 24, 25)


def evb019_cycle(
    data: ValveDashboardData, capabilities: ValveCapabilities
) -> ValveCycleStatus:
    """Interpret one complete Evb019 Dashboard using its session identity."""
    active = bool(data.regen_active) if data.regen_active in (0, 1) else None
    soaking = data.prefill_soak_mode if supports_evb019_soak(capabilities) else None
    shuttling = capabilities.is_twin_valve and data.tank_in_service in (129, 130)
    # The app interprets the byte as signed and uses values above 100 for motion.
    moving = True if shuttling else None
    if not shuttling and data.pos_time < 128:
        moving = data.pos_time > 100
    if soaking:
        remaining = data.soak_timer * 60 if 0 < data.soak_timer < 128 else None
    else:
        remaining = data.pos_time * (60 if data.pos_option_seconds == 0 else 1)
    return cycle_status(
        capabilities, active=active, position=data.regen_cycle_position,
        moving=moving, remaining=remaining, soaking=soaking,
        aerating=data.is_in_aeration if supports_aeration(capabilities, evb034=False) else None,
    )


def evb034_moving(state: int | None) -> bool | None:
    """Interpret reported motion modes and leave unconfirmed modes unknown."""
    if state in EVB034_MOVING:
        return True
    if state in _EVB034_STATIONARY:
        return False
    return None


def cycle_status(
    capabilities: ValveCapabilities, *, active: bool | None, position: int | None,
    moving: bool | None, remaining: int | None, soaking: bool | None,
    aerating: bool | None, destination: str | None = None,
) -> ValveCycleStatus:
    """Combine current readings without treating movement as arrival.

    `position` is a raw program index. `remaining` is in seconds. Pass an
    explicit destination when the protocol identifies one independently of
    position. Missing motion evidence cannot confirm a stationary step.
    """
    program = cycle_program(capabilities)
    step = None
    if soaking:
        step = "brine_soak"
    elif position == 9:
        step = "shutoff"
    elif position is not None and 0 <= position < len(program):
        step = program[position]
    if moving is not False or step is None:
        remaining = None
    elif active is False and position == 0 and not soaking:
        remaining = 0
    elif not (active or soaking):
        remaining = None
    return ValveCycleStatus(
        active, step if moving is False else None, moving,
        (destination or step) if moving else None, remaining, soaking, aerating,
    )
