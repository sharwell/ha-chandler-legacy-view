"""Production parser inputs plus discovery-to-registry metadata contracts."""

import unittest
import pytest
from homeassistant.helpers import device_registry as dr
from custom_components.chandler_legacy_view import discovery
from custom_components.chandler_legacy_view.const import DOMAIN
from packets import (
    MODERN_422, LEGACY_310, LEGACY_411, TWIN_122, EVB034_622,
    LOGGED_SHORT_ADVERTISEMENTS, FOREIGN_MANUFACTURER_ADVERTISEMENT,
    raw_advertisement,
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


@pytest.fixture
def metadata_device(hass, entry_factory):
    entry = entry_factory()
    registry = dr.async_get(hass)
    return registry.async_get_or_create(config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "valve-a")}, sw_version="C0.00")


async def test_partial_startup_preserves_presence_with_unknown_capabilities(discovery_manager, bluetooth_io):
    for raw_hex in LOGGED_SHORT_ADVERTISEMENTS:
        bluetooth_io.advertise(raw=bytes.fromhex(raw_hex))
        advertisement = discovery_manager.devices["valve-a"]
        assert (advertisement.firmware_version, advertisement.model,
                advertisement.has_connection_counter, advertisement.authentication_required) == (None, None, None, None)


@pytest.mark.parametrize("raw", [object(), "not raw bytes", [-1]])
async def test_ha_rejects_invalid_raw_before_discovery(discovery_manager, bluetooth_io, raw):
    # HA's actual service-info constructor rejects these values; parser-only
    # tests above still exercise defensive handling of malformed input.
    with pytest.raises(TypeError):
        bluetooth_io.advertise(raw=raw, manufacturer_data={1850: MODERN_422[2:]})
    assert discovery_manager.devices == {}
    bluetooth_io.advertise(manufacturer_data={1850: MODERN_422[2:]})
    assert discovery_manager.devices["valve-a"].firmware_version == 422


async def test_all_short_counter_values_preserve_trusted_metadata(discovery_manager, bluetooth_io):
    bluetooth_io.advertise(raw=raw_advertisement(MODERN_422))
    previous = discovery_manager.devices["valve-a"]
    for counter in (*range(256), 0):
        payload = bytes.fromhex("3a0701000815") + bytes((counter, 0))
        bluetooth_io.advertise(raw=raw_advertisement(payload), rssi=-67)
        current = discovery_manager.devices["valve-a"]
        assert (current.firmware_version, current.model, current.has_connection_counter,
                current.authentication_required) == (422, "Evb019", True, True)
        assert current.connection_counter == previous.connection_counter
        assert current.valve_time_minutes == previous.valve_time_minutes
        assert current.rssi == -67
        assert not current.manufacturer_data_complete


async def test_logged_fragment_preserves_effective_version(discovery_manager, bluetooth_io, caplog):
    import logging
    caplog.set_level(logging.DEBUG, logger=discovery.__name__)
    bluetooth_io.advertise(raw=raw_advertisement(MODERN_422))
    bluetooth_io.advertise(raw=bytes.fromhex(LOGGED_SHORT_ADVERTISEMENTS[-1]))
    assert discovery_manager.devices["valve-a"].firmware_version == 422
    assert "effective firmware=422, manufacturer data=partial" in caplog.text
    assert "firmware=17500" not in caplog.text


async def test_foreign_manufacturer_is_presence_only(discovery_manager, bluetooth_io):
    bluetooth_io.advertise(raw=FOREIGN_MANUFACTURER_ADVERTISEMENT,
                           manufacturer_data={1850: MODERN_422[2:]})
    assert discovery_manager.devices["valve-a"].firmware_version is None
    bluetooth_io.advertise(raw=raw_advertisement(MODERN_422))
    bluetooth_io.advertise(raw=FOREIGN_MANUFACTURER_ADVERTISEMENT, rssi=-75)
    assert discovery_manager.devices["valve-a"].firmware_version == 422
    assert discovery_manager.devices["valve-a"].rssi == -75


async def test_complete_advertisement_updates_only_owning_registry_record(hass, metadata_device, entry_factory, discovery_manager, bluetooth_io):
    registry = dr.async_get(hass)
    other_entry = entry_factory("other-entry")
    other = registry.async_get_or_create(config_entry_id=other_entry.entry_id,
        identifiers={(DOMAIN, "valve-a")}, sw_version="other")
    bluetooth_io.advertise(raw=bytes.fromhex(LOGGED_SHORT_ADVERTISEMENTS[0]))
    assert registry.async_get(metadata_device.id).sw_version is None
    assert registry.async_get(other.id).sw_version == "other"
    bluetooth_io.advertise(raw=raw_advertisement(MODERN_422))
    assert registry.async_get(metadata_device.id).sw_version == "C4.22"
    assert registry.async_get(other.id).sw_version == "other"


async def test_discovery_reload_clears_old_firmware_and_recovers(hass, metadata_device, discovery_manager, bluetooth_io):
    registry = dr.async_get(hass)
    await discovery_manager.async_unload()
    replacement = discovery.ValveDiscoveryManager(hass, "test-entry")
    await replacement.async_setup()
    try:
        bluetooth_io.advertise(raw=bytes.fromhex(LOGGED_SHORT_ADVERTISEMENTS[0]))
        assert registry.async_get(metadata_device.id).sw_version is None
        bluetooth_io.advertise(raw=raw_advertisement(MODERN_422))
        assert registry.async_get(metadata_device.id).sw_version == "C4.22"
    finally:
        await replacement.async_unload()
