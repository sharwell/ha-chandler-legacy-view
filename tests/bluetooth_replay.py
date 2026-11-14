"""Real HA discovery and scanner parsing with simulated raw Bluetooth input."""

from bluetooth_adapters import get_adapters
from bluetooth_data_tools import monotonic_time_coarse
from bleak_retry_connector import BleakSlotManager
from habluetooth.base_scanner import BaseHaRemoteScanner
from habluetooth.central_manager import CentralBluetoothManager
from homeassistant.components.bluetooth import BluetoothScanningMode
from homeassistant.components.bluetooth.manager import HomeAssistantBluetoothManager
from homeassistant.components.bluetooth.match import IntegrationMatcher
from homeassistant.components.bluetooth.storage import BluetoothStorage

from support import BluetoothIO, MODERN_422, raw_advertisement


class BluetoothReplay(BluetoothIO):
    """Feed scanner reports through real HA merging, selection, and callbacks.

    No adapter is started. The supplied monkeypatch owns the central manager's
    lifetime; call close before the HA fixture stops to release registrations.
    Inherited history and service_info also support the shared simulated GATT
    transport, without replacing HA's advertisement or availability handling.
    """

    def __init__(self, hass, monkeypatch):
        super().__init__()
        self.manager = HomeAssistantBluetoothManager(
            hass, IntegrationMatcher([]), get_adapters(), BluetoothStorage(hass),
            BleakSlotManager(),
        )
        # Supply the timer loop without starting HA's adapter/socket services.
        self.manager._loop = hass.loop
        monkeypatch.setattr(CentralBluetoothManager, "manager", self.manager)
        self.scanners = {}
        self._unregister = {}
        self.add_scanner("scanner-a")

    def add_scanner(self, source, *, connectable=True):
        """Register a simulated active scanner with a unique source name."""
        scanner = BaseHaRemoteScanner(
            source, source, connectable=connectable,
            requested_mode=BluetoothScanningMode.ACTIVE,
            current_mode=BluetoothScanningMode.ACTIVE,
        )
        self.scanners[source] = scanner
        self._unregister[source] = self.manager.async_register_scanner(scanner)

    def advertise(self, address="valve-a", *, source="scanner-a", rssi=-50,
                  payload=MODERN_422, raw=None, name="CS_Meter_Soft", observed_at=None,
                  raw_available=True):
        """Inject one report; observed_at is monotonic seconds, defaulting to now.

        Raw reports carry the supplied bytes unchanged, or a synthetic payload
        and local name. raw_available=False exercises HA's structured backend.
        Return the preferred service info after HA processes the observation.
        """
        scanner = self.scanners[source]
        when = monotonic_time_coarse() if observed_at is None else observed_at
        if raw_available:
            if raw is None:
                encoded_name = name.encode()
                raw = raw_advertisement(payload) + bytes((len(encoded_name) + 1, 9)) + encoded_name
            scanner._async_on_raw_advertisement(address, rssi, raw, {}, when)
        else:
            scanner._async_on_advertisement(
                address, rssi, name, [], {}, {1850: payload[2:]}, None, {}, when
            )
        info = self.manager.async_last_service_info(address, False)
        if info is not None:
            self.history[address] = info
        return info

    def expire(self, address="valve-a"):
        """Run production expiry checks after reports with old observed_at values.

        These checks use the real monotonic clock and HA's unchanged deadlines;
        the test supplies already-aged external observations instead of sleeping.
        """
        for scanner in self.scanners.values():
            scanner._async_expire_devices()
        self.manager._async_check_unavailable()
        if not self.manager.async_address_present(address, True):
            self.history.pop(address, None)

    def close(self):
        """Unregister all simulated scanners without touching a host adapter."""
        if self.manager._cancel_unavailable_tracking is not None:
            self.manager._cancel_unavailable_tracking.cancel()
        while self._unregister:
            _, unregister = self._unregister.popitem()
            unregister()
