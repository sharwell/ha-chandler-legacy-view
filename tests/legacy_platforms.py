"""Exercise availability fanout through real connection and platform listeners."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from enum import Enum
import importlib
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch


def _load_platform_modules() -> SimpleNamespace:
    """Load production listeners with only Home Assistant and BLE services stubbed."""

    modules: dict[str, ModuleType] = {}

    def module(name: str, **attributes) -> ModuleType:
        if name not in modules:
            modules[name] = ModuleType(name)
            if "." in name:
                parent, _, child = name.rpartition(".")
                setattr(module(parent), child, modules[name])
        vars(modules[name]).update(attributes)
        return modules[name]

    class Entity:
        hass = None

        async def async_added_to_hass(self) -> None:
            pass

        async def async_will_remove_from_hass(self) -> None:
            pass

        def async_write_ha_state(self) -> None:
            self.state_writes = getattr(self, "state_writes", 0) + 1

    module("bleak.backends.client", BaseBleakClient=object)
    module(
        "bleak_retry_connector",
        BLEAK_RETRY_EXCEPTIONS=(OSError,),
        BleakClientWithServiceCache=object,
        establish_connection=AsyncMock(),
    )
    module(
        "homeassistant.components.bluetooth",
        BluetoothChange=Enum("BluetoothChange", "ADVERTISEMENT"),
        BluetoothScanningMode=SimpleNamespace(PASSIVE="passive"),
        BluetoothServiceInfoBleak=object,
        async_register_callback=Mock(),
        async_track_unavailable=Mock(),
        async_ble_device_from_address=Mock(return_value=object()),
    )
    module("homeassistant.config_entries", ConfigEntry=object)
    module(
        "homeassistant.const",
        EVENT_HOMEASSISTANT_STARTED="homeassistant_started",
        PERCENTAGE="%",
        Platform=SimpleNamespace(
            BINARY_SENSOR="binary_sensor", NUMBER="number", SENSOR="sensor",
            SWITCH="switch",
        ),
        UnitOfTime=SimpleNamespace(SECONDS="s", DAYS="d"),
        UnitOfVolume=SimpleNamespace(GALLONS="gal"),
        UnitOfVolumeFlowRate=SimpleNamespace(GALLONS_PER_MINUTE="gal/min"),
    )
    module(
        "homeassistant.core",
        CALLBACK_TYPE=object,
        CoreState=SimpleNamespace(running="running"),
        HomeAssistant=object,
        callback=lambda function: function,
    )
    module(
        "homeassistant.helpers.entity",
        DeviceInfo=dict,
        Entity=Entity,
        EntityCategory=SimpleNamespace(CONFIG="config"),
    )
    module("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
    module(
        "homeassistant.helpers.event",
        async_call_later=Mock(),
        async_track_time_interval=Mock(return_value=Mock()),
    )
    module("homeassistant.util.dt", utcnow=lambda: datetime.now(timezone.utc))
    for platform, entity_name in (
        ("binary_sensor", "BinarySensorEntity"),
        ("sensor", "SensorEntity"),
        ("number", "NumberEntity"),
        ("switch", "SwitchEntity"),
    ):
        module(
            f"homeassistant.components.{platform}",
            **{entity_name: type(entity_name, (Entity,), {})},
        )
    module(
        "homeassistant.components.binary_sensor",
        BinarySensorDeviceClass=SimpleNamespace(PRESENCE="presence", PROBLEM="problem"),
    )
    module(
        "homeassistant.components.sensor",
        SensorDeviceClass=SimpleNamespace(TIMESTAMP="timestamp", BATTERY="battery"),
        SensorStateClass=SimpleNamespace(MEASUREMENT="measurement"),
    )

    package = "_chandler_availability_platform_tests"
    component_path = (
        Path(__file__).resolve().parents[1] / "custom_components/chandler_legacy_view"
    )
    module(package, __path__=[str(component_path)])
    module(
        f"{package}.device_registry",
        async_update_device_serial_number=Mock(),
        async_update_device_sw_version=Mock(),
    )
    with patch.dict(sys.modules, modules):
        return SimpleNamespace(**{
            name: importlib.import_module(f"{package}.{name}")
            for name in (
                "discovery", "connection", "binary_sensor", "sensor", "number", "switch",
            )
        })


production = _load_platform_modules()
