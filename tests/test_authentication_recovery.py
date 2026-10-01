"""Exercise authentication selection through real DeviceList notifications."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_advertisement_parsing import LOGGED_SHORT_ADVERTISEMENTS, discovery
from test_persistent_connection import connection_module as production


def device_list_frame(*, status: int, counter: int = 23) -> bytes:
    """Build a synthetic EVB019 response without using a device's private data."""

    packet = bytearray(20)
    packet[0:2] = b"tt"
    packet[7] = status
    packet[11] = counter
    packet[13:17] = b"\x01\x02\x03\x04"
    return bytes(packet)


def classic_device_list_frame(*, status: int = 112) -> bytes:
    """Encode a synthetic legacy PIN, 1357, using the classic wire layout."""

    packet = bytearray(device_list_frame(status=status))
    offset = status - 112
    packet[8:12] = bytes(
        (offset + 16, (offset + 15) * 2, (offset + 12) * 3, (offset + 7) * 4)
    )
    return bytes(packet)


def dashboard_frames() -> list[bytes]:
    """Return a complete synthetic dashboard reporting 7:23 and 567 gallons."""

    first, second, third = (bytearray(20) for _ in range(3))
    first[0:5] = b"uu\x00\x07\x17"
    first[11:13] = (567).to_bytes(2, "big")
    first[-1] = 57
    second[0:3] = b"uu\x01"
    second[-1] = 58
    third[0:3] = b"uu\x02"
    return [bytes(first), bytes(second), bytes(third), bytes(20), bytes(20), bytes(5) + b":"]


class AuthenticationRecoveryTests(unittest.IsolatedAsyncioTestCase):
    """Verify access decisions while exercising real request and decode code."""

    async def asyncSetUp(self) -> None:
        self.passcode = "1357"
        self.connection = production.ValveConnection(
            SimpleNamespace(loop=asyncio.get_running_loop()),
            "test-valve",
            "test-entry",
            passcode_getter=lambda _: production.ValvePasscodeConfiguration(
                value=self.passcode, is_override=True
            ),
        )
        self.partial = production.ValveAdvertisement(
            address="test-valve", name="Test valve", rssi=-50,
            manufacturer_data={}, service_data={},
            manufacturer_data_complete=False,
        )
        self.connection.update_from_advertisement(self.partial)
        self.connection.schedule_poll = Mock()
        self.connection._set_connection_cooldown = Mock()
        self.device_list_responses: deque[list[bytes]] = deque()
        self.authentication_responses: deque[list[bytes]] = deque()
        self.notification_handler = None
        self.authentication_payloads: list[bytes] = []
        self.dashboard_requests = 0
        self.dashboard_response_packets = dashboard_frames()
        self.client = SimpleNamespace(
            is_connected=True,
            disconnect=AsyncMock(),
            write_gatt_char=AsyncMock(side_effect=self.write_gatt_char),
        )
        self.connection._async_resolve_request_characteristic = AsyncMock(
            return_value=("write", {"write"})
        )
        self.connection._async_subscribe_to_notifications = AsyncMock(
            side_effect=self.subscribe
        )
        self.connection._async_unsubscribe_notifications = AsyncMock(
            side_effect=self.unsubscribe
        )
        timeout = patch.object(production, "_DEVICE_LIST_RESPONSE_TIMEOUT_SECONDS", 0.01)
        timeout.start()
        self.addCleanup(timeout.stop)
        self.addAsyncCleanup(self.connection.async_unload)

    async def subscribe(self, client, handler):
        self.assertIs(client, self.client)
        self.assertIsNone(self.notification_handler)
        self.notification_handler = handler
        return ["notify"]

    async def unsubscribe(self, client, subscriptions):
        self.assertIs(client, self.client)
        self.assertEqual(subscriptions, ["notify"])
        self.notification_handler = None

    async def write_gatt_char(self, uuid, payload, *, response):
        """Respond as the valve would, through the currently subscribed handler."""

        self.assertEqual(uuid, "write")
        self.assertTrue(response)
        if payload == bytes([production.ValveRequestCommand.DEVICE_LIST]) * 20:
            responses = self.device_list_responses.popleft()
        elif payload[:4] == b"ttPA":
            self.authentication_payloads.append(payload)
            responses = self.authentication_responses.popleft()
        elif payload == bytes([production.ValveRequestCommand.DASHBOARD]) * 20:
            self.dashboard_requests += 1
            responses = self.dashboard_response_packets
        else:
            self.fail("Unexpected outbound request")
        for packet in responses:
            self.notification_handler("notify", bytearray(packet))

    def use_counter_metadata(self) -> None:
        self.connection.update_from_advertisement(
            replace(
                self.partial, firmware_major=4, firmware_minor=22,
                firmware_version=422, model="Evb019",
                manufacturer_data_complete=True, valve_data_parsed=True,
                has_connection_counter=True, authentication_required=True,
            )
        )

    def use_classic_metadata(self) -> None:
        self.connection.update_from_advertisement(
            replace(
                self.partial, firmware_major=4, firmware_minor=18,
                firmware_version=418, model="Evb019",
                manufacturer_data_complete=True, valve_data_parsed=True,
                has_connection_counter=False, authentication_required=False,
            )
        )

    def queue_successful_authentication(self, *, counter: int = 23) -> None:
        self.device_list_responses.append([device_list_frame(status=0, counter=counter)])
        self.authentication_responses.append(
            [device_list_frame(status=128, counter=(counter + 1) & 0xFF)]
        )

    def assert_fresh_dashboard(self) -> None:
        dashboard = self.connection.dashboard_data
        self.assertIsNotNone(dashboard)
        self.assertEqual((dashboard.time_hour, dashboard.time_minute), (7, 23))
        self.assertEqual(dashboard.water_usage, 567)
        self.assertIsNone(self.notification_handler)

    async def test_partial_startup_authenticates_and_retrieves_dashboard(self) -> None:
        self.queue_successful_authentication()

        await self.connection._async_fetch_device_information(self.client)

        self.assertEqual(len(self.authentication_payloads), 1)
        self.assertEqual(self.dashboard_requests, 1)
        self.assert_fresh_dashboard()
        self.assertFalse(self.connection.authentication_lockout)
        self.connection._persistent_connection_enabled = True
        self.assertTrue(self.connection._can_start_persistent_session())

    async def test_logged_short_packet_startup_authenticates(self) -> None:
        manager = discovery.ValveDiscoveryManager(object(), "test-entry")
        self.addAsyncCleanup(manager.async_unload)
        info = SimpleNamespace(
            address="test-valve", name="CS_Meter_Soft", rssi=-50,
            raw=bytes.fromhex(LOGGED_SHORT_ADVERTISEMENTS[0]),
            manufacturer_data={}, service_data={},
        )
        with (
            patch.object(discovery, "async_track_unavailable", return_value=Mock()),
            patch.object(discovery, "async_update_device_sw_version"),
        ):
            manager._async_handle_bluetooth_event(
                info, discovery.BluetoothChange.ADVERTISEMENT
            )
        advertisement = manager.devices["test-valve"]
        self.assertIsNone(advertisement.firmware_version)
        self.assertIsNone(advertisement.authentication_required)
        self.connection.update_from_advertisement(advertisement)
        self.queue_successful_authentication(counter=0)

        await self.connection._async_fetch_device_information(self.client)

        self.assertEqual(len(self.authentication_payloads), 1)
        self.assertEqual(self.dashboard_requests, 1)
        self.assert_fresh_dashboard()

    async def test_trusted_counter_metadata_accepts_authenticated_status(self) -> None:
        self.use_counter_metadata()
        self.device_list_responses.append([device_list_frame(status=128)])

        await self.connection._async_fetch_device_information(self.client)

        self.assertEqual(self.authentication_payloads, [])
        self.assertEqual(self.dashboard_requests, 1)
        self.assert_fresh_dashboard()

    async def test_eighteen_byte_device_list_supports_authentication(self) -> None:
        self.device_list_responses.append([device_list_frame(status=0)[:18]])
        self.authentication_responses.append([device_list_frame(status=128)[:18]])

        await self.connection._async_fetch_device_information(self.client)

        self.assertEqual(len(self.authentication_payloads), 1)
        self.assertEqual(self.dashboard_requests, 1)
        self.assert_fresh_dashboard()

    async def test_unknown_authenticated_status_cannot_establish_protocol(self) -> None:
        # Status 128 also encodes a valid legacy password; it is not a unique
        # discriminator for counter-based authentication on an unknown valve.
        self.device_list_responses.append([classic_device_list_frame(status=128)])

        await self.connection._async_fetch_device_information(self.client)

        self.assertEqual(self.authentication_payloads, [])
        self.assertEqual(self.dashboard_requests, 0)
        self.assertIsNone(self.connection.dashboard_data)
        self.assertFalse(self.connection.authentication_lockout)
        self.connection._persistent_connection_enabled = True
        self.assertFalse(self.connection._can_start_persistent_session())

    async def test_malformed_device_list_does_not_authenticate_or_lock_out(self) -> None:
        packet = device_list_frame(status=0)
        self.device_list_responses.append([packet[:length] for length in (3, 8, 12, 17)])

        await self.connection._async_fetch_device_information(self.client)

        self.assertEqual(self.authentication_payloads, [])
        self.assertEqual(self.dashboard_requests, 0)
        self.assertFalse(self.connection.authentication_lockout)
        self.assertIsNone(self.notification_handler)

    async def test_unknown_status_does_not_try_pin_or_dashboard(self) -> None:
        self.device_list_responses.append([device_list_frame(status=1)])

        await self.connection._async_fetch_device_information(self.client)

        self.assertEqual(self.authentication_payloads, [])
        self.assertEqual(self.dashboard_requests, 0)
        self.assertFalse(self.connection.authentication_lockout)

    async def test_unrecognized_counter_status_does_not_try_pin(self) -> None:
        self.use_counter_metadata()
        self.device_list_responses.append([device_list_frame(status=1)])

        await self.connection._async_fetch_device_information(self.client)

        self.assertEqual(self.authentication_payloads, [])
        self.assertEqual(self.dashboard_requests, 0)
        self.assertFalse(self.connection.authentication_lockout)

    async def test_malformed_authentication_replies_do_not_lock_out_pin(self) -> None:
        self.device_list_responses.append([device_list_frame(status=0)])
        for _ in range(production._MAX_AUTHENTICATION_ATTEMPTS):
            self.authentication_responses.append([device_list_frame(status=0)[:12]])

        await self.connection._async_fetch_device_information(self.client)

        self.assertGreaterEqual(len(self.authentication_payloads), 1)
        self.assertEqual(self.dashboard_requests, 0)
        self.assertFalse(self.connection.authentication_lockout)
        self.assertIsNone(self.notification_handler)

    async def test_unrecognized_authentication_replies_do_not_lock_out_pin(self) -> None:
        self.device_list_responses.append([device_list_frame(status=0)])
        for _ in range(production._MAX_AUTHENTICATION_ATTEMPTS):
            self.authentication_responses.append([device_list_frame(status=1)])

        await self.connection._async_fetch_device_information(self.client)

        self.assertGreaterEqual(len(self.authentication_payloads), 1)
        self.assertEqual(self.dashboard_requests, 0)
        self.assertFalse(self.connection.authentication_lockout)

    async def test_repeated_valid_rejections_lock_out_pin(self) -> None:
        self.device_list_responses.append([device_list_frame(status=0)])
        for _ in range(production._MAX_AUTHENTICATION_ATTEMPTS):
            self.authentication_responses.append([device_list_frame(status=0)])

        await self.connection._async_fetch_device_information(self.client)

        self.assertEqual(len(self.authentication_payloads), production._MAX_AUTHENTICATION_ATTEMPTS)
        self.assertEqual(self.dashboard_requests, 0)
        self.assertTrue(self.connection.authentication_lockout)

    async def test_stale_unprotected_flag_cannot_bypass_counter_rejection(self) -> None:
        self.use_counter_metadata()
        self.connection.update_from_advertisement(
            replace(self.connection._advertisement, authentication_required=False)
        )
        self.connection._persistent_connection_enabled = True
        self.connection._async_send_reset_buffer_packet = AsyncMock(return_value=True)
        self.device_list_responses.append([device_list_frame(status=0)])
        for _ in range(production._MAX_AUTHENTICATION_ATTEMPTS):
            self.authentication_responses.append([device_list_frame(status=0)])

        with patch.object(production, "establish_connection", AsyncMock(return_value=self.client)):
            await self.connection.async_poll()

        self.assertEqual(len(self.authentication_payloads), production._MAX_AUTHENTICATION_ATTEMPTS)
        self.assertEqual(self.dashboard_requests, 0)
        self.assertTrue(self.connection.authentication_lockout)
        self.assertFalse(self.connection._can_start_persistent_session())
        self.assertIsNone(self.connection._persistent_task)
        self.client.disconnect.assert_awaited_once()

    async def test_classic_metadata_retains_legacy_dashboard_access(self) -> None:
        self.use_classic_metadata()
        self.device_list_responses.append([classic_device_list_frame()])

        await self.connection._async_fetch_device_information(self.client)

        self.assertEqual(self.authentication_payloads, [])
        self.assertEqual(self.dashboard_requests, 1)
        self.assert_fresh_dashboard()
        self.connection._persistent_connection_enabled = True
        self.assertTrue(self.connection._can_start_persistent_session())

    async def test_invalid_classic_decode_never_falls_back_to_pin_attempts(self) -> None:
        self.use_classic_metadata()
        for _ in range(4):
            self.device_list_responses.append([device_list_frame(status=0)])
            await self.connection._async_fetch_device_information(self.client)

        self.assertEqual(self.authentication_payloads, [])
        self.assertEqual(self.dashboard_requests, 0)
        self.assertFalse(self.connection.authentication_lockout)

    async def test_partial_update_keeps_established_counter_protocol(self) -> None:
        self.queue_successful_authentication(counter=255)
        await self.connection._async_fetch_device_information(self.client)
        self.connection.update_from_advertisement(replace(self.partial, connection_counter=0))
        self.device_list_responses.append([device_list_frame(status=128, counter=0)])

        await self.connection._async_fetch_device_information(self.client)

        self.assertEqual(len(self.authentication_payloads), 1)
        self.assertEqual(self.dashboard_requests, 2)
        self.assert_fresh_dashboard()

    async def test_new_session_cannot_reuse_previous_authentication(self) -> None:
        self.queue_successful_authentication()
        self.device_list_responses.append([])
        self.connection._async_send_reset_buffer_packet = AsyncMock(return_value=True)
        with patch.object(production, "establish_connection", AsyncMock(return_value=self.client)):
            await self.connection.async_poll()
            await self.connection.async_poll()

        self.assertEqual(len(self.authentication_payloads), 1)
        self.assertEqual(self.dashboard_requests, 1)
        self.assertEqual(self.client.disconnect.await_count, 2)
        self.connection._persistent_connection_enabled = True
        self.assertFalse(self.connection._can_start_persistent_session())

    async def test_last_success_requires_fresh_dashboard(self) -> None:
        self.queue_successful_authentication()
        self.device_list_responses.extend((
            [device_list_frame(status=1)],
            [],
            [device_list_frame(status=128)],
        ))
        self.connection._async_send_reset_buffer_packet = AsyncMock(return_value=True)
        first_success = datetime(2026, 9, 30, 9, tzinfo=timezone.utc)
        with (
            patch.object(production, "establish_connection", AsyncMock(return_value=self.client)),
            patch.object(production, "_DASHBOARD_RESPONSE_TIMEOUT_SECONDS", 0.01),
        ):
            with patch.object(production.dt_util, "utcnow", return_value=first_success):
                await self.connection.async_poll()
            self.assertEqual(self.connection.last_success, first_success)
            self.assert_fresh_dashboard()

            # A decode failure, a DeviceList timeout, and an authenticated
            # DeviceList followed by a Dashboard timeout all retain the last
            # actual reading's timestamp.
            self.dashboard_response_packets = []
            for minutes in (1, 2, 3):
                with patch.object(
                    production.dt_util, "utcnow",
                    return_value=first_success + timedelta(minutes=minutes),
                ):
                    await self.connection.async_poll()
                self.assertEqual(self.connection.last_success, first_success)

        self.assertEqual(self.dashboard_requests, 2)
        self.assertEqual(self.client.disconnect.await_count, 4)

    async def test_unresolved_first_poll_has_no_success_timestamp(self) -> None:
        self.device_list_responses.append([device_list_frame(status=128)])
        self.connection._async_send_reset_buffer_packet = AsyncMock(return_value=True)

        with patch.object(production, "establish_connection", AsyncMock(return_value=self.client)):
            await self.connection.async_poll()

        self.assertEqual(self.dashboard_requests, 0)
        self.assertIsNone(self.connection.dashboard_data)
        self.assertIsNone(self.connection.last_success)
        self.client.disconnect.assert_awaited_once()

    async def test_logs_omit_pin_and_authentication_payload(self) -> None:
        self.queue_successful_authentication()
        with self.assertLogs(production._LOGGER.name, level="DEBUG") as captured:
            await self.connection._async_fetch_device_information(self.client)

        log = "\n".join(captured.output)
        self.assertNotIn(self.passcode, log)
        self.assertNotIn("digits=", log)
        self.assertEqual(len(self.authentication_payloads), 1)
        self.assertNotIn(self.authentication_payloads[0].hex(), log)

    async def test_logs_omit_decoded_classic_pin(self) -> None:
        self.use_classic_metadata()
        self.device_list_responses.append([classic_device_list_frame()])
        with self.assertLogs(production._LOGGER.name, level="DEBUG") as captured:
            await self.connection._async_fetch_device_information(self.client)
        self.assertNotIn(self.passcode, "\n".join(captured.output))

    async def test_logs_omit_invalid_configured_pin(self) -> None:
        self.passcode = "invalid-secret"
        self.use_counter_metadata()
        self.device_list_responses.append([device_list_frame(status=0)])
        with self.assertLogs(production._LOGGER.name, level="DEBUG") as captured:
            await self.connection._async_fetch_device_information(self.client)

        self.assertNotIn(self.passcode, "\n".join(captured.output))
        self.assertEqual(self.authentication_payloads, [])
        self.assertEqual(self.dashboard_requests, 0)


if __name__ == "__main__":
    unittest.main()
