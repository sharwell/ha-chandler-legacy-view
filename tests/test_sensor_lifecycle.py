"""Exercise dashboard subscriptions for enabled and disabled sensors."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_availability_platforms import production


class SensorLifecycleTests(unittest.IsolatedAsyncioTestCase):
    """Disabled sensors must not subscribe to the shared dashboard poller."""

    async def test_dashboard_subscriptions_follow_entity_lifecycle(self) -> None:
        now = datetime(2026, 9, 29, 17, tzinfo=timezone.utc)
        self.enterContext(
            patch.object(production.sensor.dt_util, "now", return_value=now, create=True)
        )
        advertisement = production.connection.ValveAdvertisement(
            address="valve", name="CS_Meter_Soft", rssi=-50,
            manufacturer_data={}, service_data={},
        )

        for sensor_type in (
            production.sensor.ValveTimeOfDaySensor,
            production.sensor.ValveWaterUsageTodaySensor,
        ):
            with self.subTest(sensor=sensor_type.__name__):
                hass = SimpleNamespace(loop=asyncio.get_running_loop())
                connection = production.connection.ValveConnection(
                    hass, advertisement.address, "test-entry"
                )
                sensor = sensor_type(advertisement, connection)

                def receive_dashboard(minute: int) -> None:
                    first = bytearray(20)
                    first[3:6] = bytes((4, minute, 1))
                    first[12] = minute
                    connection._handle_dashboard_packets(
                        [bytes(first), bytes(20), bytes(20), bytes(20), bytes(20), bytes(6)]
                    )

                # HA constructs disabled entities but does not add them.
                # The connection must still retain fresh clock data.
                receive_dashboard(35)
                self.assertEqual(connection.dashboard_data.time_minute, 35)
                self.assertIsNone(sensor._attr_native_value)
                self.assertEqual(connection._dashboard_listeners, [])

                # An enabled entity starts with the latest cached value.
                sensor.hass = hass
                await sensor.async_added_to_hass()
                expected = (
                    now.replace(hour=16, minute=35)
                    if sensor_type is production.sensor.ValveTimeOfDaySensor else 35
                )
                self.assertEqual(sensor._attr_native_value, expected)
                await asyncio.sleep(0)  # Drain the listener's initial notification.
                self.assertEqual(len(connection._dashboard_listeners), 1)
                writes = sensor.state_writes

                receive_dashboard(36)
                expected = (
                    now.replace(hour=16, minute=36)
                    if sensor_type is production.sensor.ValveTimeOfDaySensor else 36
                )
                self.assertEqual(sensor._attr_native_value, expected)
                self.assertEqual(sensor.state_writes, writes + 1)

                # Removal stops callbacks without stopping shared data collection.
                await sensor.async_will_remove_from_hass()
                self.assertEqual(connection._dashboard_listeners, [])
                receive_dashboard(37)
                self.assertEqual(connection.dashboard_data.time_minute, 37)
                self.assertEqual(sensor._attr_native_value, expected)
                self.assertEqual(sensor.state_writes, writes + 1)


if __name__ == "__main__":
    unittest.main()
