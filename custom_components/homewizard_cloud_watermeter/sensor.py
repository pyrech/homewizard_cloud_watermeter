import logging
from homeassistant.components.sensor import (
    EntityCategory,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.const import PERCENTAGE

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]

    if not coordinator.data:
        _LOGGER.error("No devices found, sensors will not be created")

    entities = []

    # Create a sensor for each homewizard device
    for value in coordinator.data:
        entities.append(HomeWizardCloudWaterSensor(coordinator, value))
        entities.append(HomeWizardWifiSensor(coordinator, value))
        entities.append(HomeWizardOnlineSensor(coordinator, value))

    async_add_entities(entities)

class HomeWizardBaseSensor(CoordinatorEntity):
    """Common base for all HomeWizard sensors."""

    def __init__(self, coordinator, value):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._device = value["device"]
        self._sanitized_identifier = value["device"]["sanitized_identifier"]

        # Unique ID needs to be unique per entity, so we append the class name or a suffix
        # This will be overridden or extended in child classes
        self._attr_unique_id = f"{self._sanitized_identifier}_{self.__class__.__name__}"

    @property
    def device_info(self):
        """Return device information to group all entities under the same device."""
        return {
            "identifiers": {(DOMAIN, self._sanitized_identifier)},
            "name": self._device.get("name", "HomeWizard Watermeter"),
            "manufacturer": "HomeWizard",
            "model": self._device.get("model"),
            "hw_version": self._device.get("hardwareVersion"),
        }

class HomeWizardCloudWaterSensor(HomeWizardBaseSensor, SensorEntity):
    def __init__(self, coordinator, data):
        super().__init__(coordinator, data)

        self._daily_total = data["daily_total"]
        self._device = data["device"]
        self._attr_name = "Daily usage"
        self._attr_unique_id = f"{self._device['sanitized_identifier']}_daily_total"
        self._attr_device_class = SensorDeviceClass.WATER
        self._attr_native_unit_of_measurement = data["unit"]

        # Use TOTAL for daily values that reset at midnight
        self._attr_state_class = SensorStateClass.TOTAL

    @property
    def native_value(self):
        """Return the state of the sensor as a float."""
        if not self._daily_total:
            return None

        try:
            return float(self._daily_total)
        except ValueError:
            _LOGGER.warning("Could not convert value '%s' to float", self._daily_total)
            return None

class HomeWizardWifiSensor(HomeWizardBaseSensor, SensorEntity):
    _attr_name = "Wifi Signal"
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    @property
    def native_value(self):
        return self._device.get("wifiStrength", 0)

class HomeWizardOnlineSensor(HomeWizardBaseSensor, SensorEntity):
    _attr_name = "Online state"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    @property
    def native_value(self):
        return self._device.get("onlineState", "Unknown")
