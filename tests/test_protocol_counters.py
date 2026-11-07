"""Firmware-specific challenges must authenticate real manager-owned sessions."""

import pytest
from homeassistant.helpers import entity_registry as er

from custom_components.chandler_legacy_view.const import DATA_CONNECTION_MANAGER, DOMAIN
from custom_components.chandler_legacy_view.models import ValveAdvertisement
from packets import MODERN_422, TWIN_122, dashboard_frames


@pytest.mark.parametrize("firmware,twin", [(419, False), (420, False), (422, False), (122, True)])
@pytest.mark.parametrize("counter", [0, 23, 255])
async def test_versioned_counter_authenticates_across_reconnects(
    hass, runtime_factory, bluetooth_io, transport, clock, firmware, twin, counter,
):
    # Water System 6.18.2 CsDeviceListPacket.update distinguishes C4.19
    # from C4.20+. Deliberately different fields make the wrong source reject
    # a correct PIN instead of letting an overly permissive fake hide it.
    entry = await runtime_factory()
    valve = transport.valve()
    valve.firmware_version = firmware
    valve.is_twin_valve = twin
    valve.connection_counter = counter
    valve.legacy_reserved_counter_byte = (counter + 17) & 255
    for poll, gallons in enumerate((135, 246)):
        if poll:
            await clock.advance(31)
        payload = bytearray(TWIN_122 if twin else MODERN_422)
        payload[-2:] = bytes((firmware // 100, int(f"{firmware % 100:02d}", 16)))
        payload[6] = (counter + poll + (0 if firmware == 419 else 97)) & 255
        bluetooth_io.advertise(manufacturer_data={1850: bytes(payload[2:])})
        await hass.async_block_till_done()
        connection = hass.data[DOMAIN][entry.entry_id][DATA_CONNECTION_MANAGER].get_connection("valve-a")
        valve.dashboards.append(dashboard_frames(gallons=gallons))
        await clock.run(connection.async_poll())
        registry = er.async_get(hass)
        usage = registry.async_get_entity_id("sensor", DOMAIN, "valve-a_water_usage_today")
        lockout = registry.async_get_entity_id("switch", DOMAIN, "valve-a_authentication_lockout")
        assert hass.states.get(usage).state == str(gallons)
        assert hass.states.get(lockout).state == "off"
        assert len(valve.authentication_payloads) == poll + 1
        assert connection.last_success is not None
        assert not valve.clients[-1].is_connected


async def test_c419_without_advertisement_counter_does_not_guess_or_lock_out(
    connection_factory, transport, clock,
):
    connection = connection_factory(advertisement=ValveAdvertisement(
        address="valve-a", name="CS_Meter_Soft", rssi=-50,
        manufacturer_data={}, service_data={}, model="Evb019", firmware_version=419,
        has_connection_counter=True, authentication_required=True,
    ))
    valve = transport.valve()
    valve.firmware_version = 419
    await clock.run(connection.async_poll())
    assert valve.authentication_payloads == []
    assert connection.dashboard_data is None
    assert not connection.authentication_lockout
