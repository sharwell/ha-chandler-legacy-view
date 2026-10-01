"""Advertisement regression cases from live valve logs and supported layouts."""

from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from test_device_registry import helpers as registry_helpers, production
from test_discovery_availability import discovery_module as discovery


MODERN_422 = bytes.fromhex("3a070100082aae00010203040422")
LEGACY_310 = bytes.fromhex("3a070100082a0102040310")
LEGACY_411 = bytes.fromhex("3a070100082a010203040411")
TWIN_122 = bytes.fromhex("3a070100082aae64010203040122")
EVB034_622 = bytes.fromhex("3a07e000082a04020622")

# Captured before/after the registry and sensor changes. These contain no PINs.
LOGGED_SHORT_ADVERTISEMENTS = (
    "02010609ff3a070100081500000e0943535f4d657465725f536f6674",
    "02010609ff3a070100081600000e0943535f4d657465725f536f6674",
    "02010609ff3a070100081900000d0943535f42575f46696c746572",
    "02010609ff3a070100081700000e0943535f4d657465725f536f6674",
    "02010609ff3a070100081a00000d0943535f42575f46696c746572",
    "02010608ff3a07191a1b1c1d0d0943535f42575f46696c746572",
    "02010609ff3a0701000527ae000d0943535f42575f46696c746572",
    "02010609ff3a0701000528af000d0943535f42575f46696c746572",
)
FOREIGN_MANUFACTURER_ADVERTISEMENT = bytes.fromhex(
    "02010609ff59000100052552000e0943535f4d657465725f536f6674"
)


def raw_advertisement(*segments: bytes) -> bytes:
    """Wrap company-prefixed payloads in Bluetooth manufacturer AD structures."""

    return b"\x02\x01\x06" + b"".join(
        bytes((len(segment) + 1, 0xFF)) + segment for segment in segments
    )


class AdvertisementParsingTests(unittest.TestCase):
    """Only a complete supported record can establish firmware and capabilities."""

    def classify(self, payload: bytes):
        return discovery._classify_manufacturer_data(
            {1850: payload[2:]}, raw_advertisement(payload)
        )

    def assert_unknown(self, classification):
        self.assertTrue(classification.is_csi_device)
        self.assertFalse(classification.manufacturer_data_complete)
        self.assertFalse(classification.valve_data_parsed)
        for field in (
            "firmware_major", "firmware_minor", "firmware_version", "model",
            "has_connection_counter", "authentication_required",
            "connection_counter", "valve_status", "valve_type_full",
        ):
            self.assertIsNone(getattr(classification, field), field)

    def test_exact_logged_short_packets_do_not_publish_metadata(self):
        for raw_hex in LOGGED_SHORT_ADVERTISEMENTS:
            with self.subTest(raw=raw_hex):
                self.assert_unknown(discovery._classify_manufacturer_data(
                    {1850: MODERN_422[2:]}, bytes.fromhex(raw_hex)
                ))

    def test_every_short_counter_value_stays_unknown(self):
        for counter in range(256):
            with self.subTest(counter=counter):
                self.assert_unknown(self.classify(
                    bytes.fromhex("3a0701000815") + bytes((counter, 0))
                ))

    def test_supported_complete_formats(self):
        for payload, version, model, has_counter in (
            (MODERN_422, 422, "Evb019", True),
            (LEGACY_310, 310, "Evb019", False),
            (LEGACY_411, 411, "Evb019", False),
            (TWIN_122, 122, "Evb019", True),
            (EVB034_622, 622, "Evb034", True),
        ):
            with self.subTest(version=version):
                result = self.classify(payload)
                self.assertTrue(result.manufacturer_data_complete)
                self.assertTrue(result.valve_data_parsed)
                self.assertEqual(result.firmware_version, version)
                self.assertEqual(result.model, model)
                self.assertIs(result.has_connection_counter, has_counter)
                self.assertEqual(result.valve_time_hours, 8)
                self.assertEqual(result.valve_time_minutes, 42)
                self.assertEqual(result.valve_type_full, 4)
                self.assertIs(result.authentication_required, model == "Evb019")

    def test_complete_counter_zero_does_not_change_firmware(self):
        for counter in (254, 255, 0, 1):
            payload = bytearray(MODERN_422)
            payload[6] = counter
            result = self.classify(bytes(payload))
            self.assertEqual(result.firmware_version, 422)
            self.assertEqual(result.connection_counter, counter)

    def test_layout_and_firmware_must_agree(self):
        invalid = (
            MODERN_422[:-2] + b"\x00\x00",
            MODERN_422[:-2] + b"\x03\x10",
            MODERN_422[:-2] + b"\x06\x22",
            MODERN_422[:-2] + b"\x01\x22",  # Twin marker is missing.
            EVB034_622[:-2] + b"\x04\x22",
            LEGACY_310[:-2] + b"\x04\x22",
            LEGACY_411[:-2] + b"\x04\x22",
            MODERN_422 + b"\x00",
        )
        for payload in invalid:
            with self.subTest(payload=payload.hex()):
                self.assert_unknown(self.classify(payload))

    def test_raw_data_does_not_require_structured_copy(self):
        result = discovery._classify_manufacturer_data(
            {}, raw_advertisement(MODERN_422)
        )
        self.assertEqual(result.firmware_version, 422)

    def test_structured_data_without_raw_uses_company_key(self):
        for payload in (MODERN_422, LEGACY_310, LEGACY_411, TWIN_122, EVB034_622):
            with self.subTest(payload=payload.hex()):
                self.assertEqual(
                    discovery._classify_manufacturer_data({1850: payload[2:]}, None),
                    self.classify(payload),
                )
        # HA excludes the company identifier; do not guess away duplicated bytes.
        self.assert_unknown(discovery._classify_manufacturer_data(
            {1850: MODERN_422}, None
        ))

    def test_current_foreign_manufacturer_never_uses_cached_chandler_record(self):
        self.assert_unknown(discovery._classify_manufacturer_data(
            {1850: MODERN_422[2:], 89: b"\x01\x00\x05\x25\x52\x00"},
            FOREIGN_MANUFACTURER_ADVERTISEMENT,
        ))

    def test_segmented_complete_record_is_reconstructed_within_one_packet(self):
        result = discovery._classify_manufacturer_data(
            {}, raw_advertisement(MODERN_422[:8], MODERN_422[:2] + MODERN_422[8:])
        )
        self.assertEqual(result, self.classify(MODERN_422))

    def test_truncated_or_invalid_raw_never_falls_back_to_cached_data(self):
        valid = raw_advertisement(MODERN_422)
        for raw in (
            valid[:-1], valid + b"\x05\xff\x3a", b"", b"\x02\x01\x06",
            "not raw bytes", object(),
        ):
            with self.subTest(raw=raw):
                self.assert_unknown(discovery._classify_manufacturer_data(
                    {1850: MODERN_422[2:]}, raw
                ))

    def test_invalid_structured_data_remains_unknown(self):
        for value in (None, b"", "not bytes", 4, [1, 2, 3], {"old": MODERN_422}):
            with self.subTest(value=value):
                self.assert_unknown(discovery._classify_manufacturer_data(
                    {1850: value}, None
                ))


class AdvertisementMetadataTests(unittest.TestCase):
    """Partial observations update presence without changing accepted metadata."""

    def setUp(self):
        self.manager = discovery.ValveDiscoveryManager(object(), "entry")
        self.enterContext(patch.object(discovery, "async_track_unavailable", return_value=Mock()))
        self.update_version = self.enterContext(
            patch.object(discovery, "async_update_device_sw_version")
        )

    def advertise(self, raw: bytes, *, rssi=-50, manufacturer_data=None):
        info = SimpleNamespace(
            address="valve", name="CS_Meter_Soft", rssi=rssi, raw=raw,
            manufacturer_data=manufacturer_data or {}, service_data={},
        )
        self.manager._async_handle_bluetooth_event(
            info, discovery.BluetoothChange.ADVERTISEMENT
        )
        return self.manager.devices["valve"]

    def test_partial_startup_preserves_presence_with_unknown_capabilities(self):
        for raw_hex in LOGGED_SHORT_ADVERTISEMENTS:
            advertisement = self.advertise(bytes.fromhex(raw_hex))
            self.assertIsNone(advertisement.firmware_version)
            self.assertIsNone(advertisement.model)
            self.assertIsNone(advertisement.has_connection_counter)
            self.assertIsNone(advertisement.authentication_required)

    def test_invalid_present_raw_does_not_enable_structured_fallback(self):
        for raw in (object(), "not raw bytes", [-1]):
            with self.subTest(raw=raw):
                current = self.advertise(
                    raw, manufacturer_data={1850: MODERN_422[2:]}
                )
                self.assertIsNone(current.firmware_version)
                self.assertIsNone(current.has_connection_counter)
                self.assertFalse(current.manufacturer_data_complete)

        # The same structured record can establish metadata when raw is absent.
        current = self.advertise(None, manufacturer_data={1850: MODERN_422[2:]})
        self.assertEqual(current.firmware_version, 422)

    def test_all_short_counter_values_preserve_trusted_metadata(self):
        previous = self.advertise(raw_advertisement(MODERN_422))
        for counter in (*range(256), 0):
            payload = bytes.fromhex("3a0701000815") + bytes((counter, 0))
            current = self.advertise(raw_advertisement(payload), rssi=-67)
            self.assertEqual(current.firmware_version, 422)
            self.assertEqual(current.model, "Evb019")
            self.assertIs(current.has_connection_counter, True)
            self.assertIs(current.authentication_required, True)
            self.assertEqual(current.connection_counter, previous.connection_counter)
            self.assertEqual(current.valve_time_minutes, previous.valve_time_minutes)
            self.assertEqual(current.rssi, -67)
            self.assertFalse(current.manufacturer_data_complete)

    def test_logged_fragment_preserves_firmware_and_logs_effective_value(self):
        self.advertise(raw_advertisement(MODERN_422))
        with self.assertLogs(discovery.__name__, level="DEBUG") as logs:
            current = self.advertise(bytes.fromhex(LOGGED_SHORT_ADVERTISEMENTS[-1]))
        self.assertEqual(current.firmware_version, 422)
        self.assertIn("effective firmware=422, manufacturer data=partial", "\n".join(logs.output))
        self.assertNotIn("firmware=17500", "\n".join(logs.output))

    def test_foreign_manufacturer_is_presence_only_and_keeps_trusted_metadata(self):
        current = self.advertise(
            FOREIGN_MANUFACTURER_ADVERTISEMENT,
            manufacturer_data={1850: MODERN_422[2:]},
        )
        self.assertIsNone(current.firmware_version)
        self.advertise(raw_advertisement(MODERN_422))
        current = self.advertise(FOREIGN_MANUFACTURER_ADVERTISEMENT, rssi=-75)
        self.assertEqual(current.firmware_version, 422)
        self.assertEqual(current.rssi, -75)

    def test_complete_advertisement_recovers_unknown_metadata(self):
        self.advertise(bytes.fromhex(LOGGED_SHORT_ADVERTISEMENTS[0]))
        recovered = self.advertise(raw_advertisement(MODERN_422))
        self.assertEqual(recovered.firmware_version, 422)
        self.assertIs(recovered.has_connection_counter, True)
        self.assertTrue(recovered.manufacturer_data_complete)
        self.assertEqual(self.update_version.call_args.args[-1], "422")

    def test_reload_clears_persisted_zero_version_and_recovers_from_complete_data(self):
        device = SimpleNamespace(id="registered-valve", sw_version="C0.00")
        registry = SimpleNamespace(
            async_get_device_by_identifier=Mock(return_value=device),
            async_update_device=Mock(
                side_effect=lambda device_id, **changes: vars(device).update(changes)
            ),
        )
        with (
            patch.object(registry_helpers.dr, "async_get", return_value=registry),
            patch.object(
                discovery, "async_update_device_sw_version",
                registry_helpers.async_update_device_sw_version,
            ),
            patch.object(
                discovery, "format_firmware_version",
                production.discovery.format_firmware_version,
            ),
        ):
            self.advertise(bytes.fromhex(LOGGED_SHORT_ADVERTISEMENTS[0]))
            self.assertIsNone(device.sw_version)
            registry.async_get_device_by_identifier.assert_called_with(
                (registry_helpers.DOMAIN, "valve"), "entry"
            )
            registry.async_update_device.assert_called_once_with(
                device.id, sw_version=None
            )

            self.advertise(raw_advertisement(MODERN_422))
            self.assertEqual(device.sw_version, "C4.22")
            registry.async_update_device.assert_called_with(
                device.id, sw_version="C4.22"
            )


if __name__ == "__main__":
    unittest.main()
