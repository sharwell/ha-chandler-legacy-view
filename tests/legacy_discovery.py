"""Discovery availability regressions without Home Assistant installed."""

from __future__ import annotations

from enum import Enum
import importlib
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch


def _load_discovery_module():
    """Import real discovery with HA's advertisement-only event contract."""

    modules: dict[str, ModuleType] = {}

    def module(name: str, **attributes) -> ModuleType:
        if name not in modules:
            modules[name] = ModuleType(name)
            if "." in name:
                parent, _, child = name.rpartition(".")
                setattr(module(parent), child, modules[name])
        vars(modules[name]).update(attributes)
        return modules[name]

    module(
        "homeassistant.components.bluetooth",
        BluetoothChange=Enum("BluetoothChange", "ADVERTISEMENT"),
        BluetoothScanningMode=SimpleNamespace(PASSIVE="passive"),
        BluetoothServiceInfoBleak=SimpleNamespace,
        async_register_callback=Mock(),
        async_track_unavailable=Mock(),
    )
    module(
        "homeassistant.const",
        Platform=SimpleNamespace(
            BINARY_SENSOR="binary_sensor", NUMBER="number", SENSOR="sensor",
            SWITCH="switch",
        ),
    )
    module(
        "homeassistant.core", CALLBACK_TYPE=object, HomeAssistant=object,
        callback=lambda function: function,
    )

    package = "_chandler_discovery_tests"
    component_path = (
        Path(__file__).resolve().parents[1] / "custom_components/chandler_legacy_view"
    )
    module(package, __path__=[str(component_path)])
    module(f"{package}.device_registry", async_update_device_sw_version=Mock())
    module(
        f"{package}.entity",
        _is_clack_valve=lambda name: name.casefold().startswith("cl_"),
        format_firmware_version=lambda advertisement: str(
            advertisement.firmware_version
        ),
    )
    with patch.dict(sys.modules, modules):
        return importlib.import_module(f"{package}.discovery")


discovery_module = _load_discovery_module()
