"""Home Assistant entities backed by a valve's confirmed cycle readings."""

from collections.abc import Callable

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .connection import ValveConnection
from .const import DATA_CONNECTION_MANAGER, DATA_DISCOVERY_DEVICE_ID, DATA_DISCOVERY_MANAGER, DOMAIN
from .cycle import CYCLE_READING, cycle_program, supports_aeration, supports_evb019_soak, supports_soak
from .discovery import ValveDiscoveryChange
from .entity import ChandlerValveEntity
from .models import ValveAdvertisement, ValveCycleStatus


class ValveCycleEntity(ChandlerValveEntity):
    """Subscribe an enabled entity to its valve's cycle and freshness updates."""

    def __init__(self, advertisement: ValveAdvertisement, connection: ValveConnection,
                 field: str, suffix: str, name: str) -> None:
        """Bind a reading field and stable registry suffix to the valve's connection."""
        super().__init__(advertisement)
        self._connection = connection
        self._field = field
        self._attr_unique_id = f"{advertisement.address}_{suffix}"
        self._attr_name = self._reading_name = name

    @property
    def _cycle(self) -> ValveCycleStatus | None:
        data = self._connection.dashboard_data
        return data.cycle if data is not None else None

    @property
    def _evb034(self) -> bool:
        return self._advertisement.model == "Evb034"

    @property
    def supported(self) -> bool:
        """Return whether current model and program evidence supports this entity."""
        capabilities = self._connection.entity_capabilities
        if not cycle_program(capabilities) or (
            self._evb034 and capabilities.is_clack_valve is None
        ):
            return False
        if self._field == "soaking":
            if self._evb034:
                return supports_soak(capabilities) and self._value is not None
            return supports_evb019_soak(capabilities)
        if self._field == "aerating":
            return supports_aeration(capabilities, evb034=self._evb034)
        return True

    @property
    def _value(self):
        data = self._cycle
        return getattr(data, self._field) if data is not None else None

    @property
    def available(self) -> bool:
        """Require presence, support, and a current value for this reading."""
        return (
            self._connection.available and self.supported
            and self._connection.reading_is_current(CYCLE_READING)
            and self._value is not None
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to cycle, capability, and availability notifications."""
        await super().async_added_to_hass()
        self.async_on_remove(self._connection.add_dashboard_listener(self._publish))
        self.async_on_remove(self._connection.add_freshness_listener(self._publish))
        self.async_on_remove(self._connection.add_capability_listener(self._publish))

    async def async_update(self) -> None:
        """Request a fresh, shared Dashboard read through the valve connection."""
        await self._connection.async_refresh()

    @callback
    def _publish(self, *_args) -> None:
        if self.hass is not None and self.platform is not None:
            self.async_write_ha_state()

    @callback
    def async_handle_discovery(self, advertisement: ValveAdvertisement) -> None:
        """Update device metadata and publish connection availability."""
        self.async_update_from_advertisement(advertisement)
        self._attr_name = self._reading_name
        self._publish()


class ValveCycleBinarySensor(ValveCycleEntity, BinarySensorEntity):
    """Report a confirmed boolean phase or movement indication."""

    @property
    def is_on(self) -> bool | None:
        """Return the phase indication, or None when the valve has no value."""
        return self._value

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        """Show a known destination only while the movement entity reports On."""
        if self._field == "moving" and self.available and self.is_on and self._cycle.moving_to:
            return {"moving_to": self._cycle.moving_to}
        return None


class ValveCurrentCycleSensor(ValveCycleEntity, SensorEntity):
    """Report the named stationary cycle position as a Home Assistant enum."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_translation_key = "current_cycle"

    def __init__(self, advertisement: ValveAdvertisement, connection: ValveConnection) -> None:
        """Bind the current-cycle enum to the supplied valve."""
        super().__init__(advertisement, connection, "step", "current_cycle", "Current cycle")

    @property
    def native_value(self) -> str | None:
        """Return the confirmed step ID, or None during motion or missing data."""
        return self._value

    @property
    def options(self) -> list[str]:
        """Return the names that the valve can report for its current program."""
        capabilities = self._connection.entity_capabilities
        options = list(cycle_program(capabilities))
        dashboard = self._connection.dashboard_data
        if (dashboard and dashboard.shutoff_setting_enabled) or self._value == "shutoff":
            options.append("shutoff")
        if (supports_soak(capabilities) if self._evb034 else supports_evb019_soak(capabilities)):
            options.append("brine_soak")
        return options


class ValveCycleTimeSensor(ValveCycleEntity, SensorEntity):
    """Report the confirmed stationary step's remaining duration in seconds."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS

    def __init__(self, advertisement: ValveAdvertisement, connection: ValveConnection) -> None:
        """Bind the shared cycle and brine-soak timer to the supplied valve."""
        super().__init__(advertisement, connection, "remaining", "cycle_time_remaining", "Cycle time remaining")

    @property
    def native_value(self) -> int | None:
        """Return whole seconds, or None when a numeric estimate is unavailable."""
        return self._value


CycleEntities = Callable[[ValveAdvertisement, ValveConnection], list[ValveCycleEntity]]


@callback
def async_add_cycle_entities(hass: HomeAssistant, entry: ConfigEntry,
                             add_entities: AddEntitiesCallback,
                             create_entities: CycleEntities) -> None:
    """Add eligible cycle entities now or when confirmed identity arrives.

    A platform provides its factory. Register one set per valve and retain
    existing IDs if eligibility changes. Entry unload removes discovery and
    acquisition listeners. Enabled entities own their reading listeners.
    """
    entry_data = hass.data[DOMAIN][entry.entry_id]
    discover = entry_data[DATA_DISCOVERY_MANAGER]
    connections = entry_data[DATA_CONNECTION_MANAGER]
    devices: dict[str, list[ValveCycleEntity]] = {}
    registered: set[str] = set()

    @callback
    def register(advertisement: ValveAdvertisement, change=None) -> None:
        address = advertisement.address
        connection = connections.get_connection(address)
        if connection is None:
            return
        entities = devices.get(address)
        if entities is None:
            entities = devices[address] = create_entities(advertisement, connection)

            @callback
            def ensure_supported(*_args) -> None:
                added = [entity for entity in entities
                         if entity.unique_id not in registered and entity.supported]
                for entity in added:
                    entity.async_set_via_device_id(entry_data[DATA_DISCOVERY_DEVICE_ID])
                    registered.add(entity.unique_id)
                if added:
                    add_entities(added)

            entry.async_on_unload(connection.add_capability_listener(ensure_supported))
            entry.async_on_unload(connection.add_dashboard_listener(ensure_supported))
            ensure_supported()
        for entity in entities:
            entity.async_handle_discovery(advertisement)

    for advertisement in discover.devices.values():
        register(advertisement)
    entry.async_on_unload(discover.async_add_listener(register))
