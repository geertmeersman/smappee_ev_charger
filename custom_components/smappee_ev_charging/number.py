import logging
import asyncio
from homeassistant.components.number import NumberEntity, NumberDeviceClass, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.const import UnitOfElectricCurrent, PERCENTAGE
from homeassistant.helpers.entity import EntityCategory

from .sensor import SmappeeBaseEntity
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Dynamically set up Smappee number entities based on discovered smart devices."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    client = entry_data["client"]
    coordinator = entry_data["coordinator"]

    entities = []

    if coordinator.data and "smart_devices" in coordinator.data:
        for device in coordinator.data["smart_devices"]:
            category = device.get("type", {}).get("category")
            device_id = device.get("id")

            if not device_id:
                continue

            if category == "CARCHARGER":
                _LOGGER.debug("Creating dynamic configuration numbers for Smappee charger: %s", device_id)
                entities.extend([
                    # 1. Max Current Configuration Slider
                    SmappeeConfigPropertyNumber(
                        coordinator, client, entry.title, device_id, "charger",
                        property_name="etc.smart.device.type.car.charger.config.max.current",
                        translation_key="max_current_setting",
                        value_type="Quantity",
                        unit=UnitOfElectricCurrent.AMPERE,
                        device_class=NumberDeviceClass.CURRENT,
                        default_min=6.0, default_max=32.0
                    ),
                    # 2. Minimum Solar Excess Slider
                    SmappeeConfigPropertyNumber(
                        coordinator, client, entry.title, device_id, "charger",
                        property_name="etc.smart.device.type.car.charger.config.min.excesspct",
                        translation_key="min_excess_percentage",
                        value_type="Integer",
                        unit=PERCENTAGE,
                        icon="mdi:sun-wireless",
                        default_min=0.0, default_max=100.0
                    ),
                    # 3. Unique specific custom logic entities
                    SmappeeOfflineFailsafeNumber(coordinator, client, entry.title, device_id),
                    SmappeeChargePercentageSlider(coordinator, client, entry.title, device_id),
                ])
            
            elif category == "LED":
                _LOGGER.debug("Creating dynamic LED brightness configuration slider for: %s", device_id)
                entities.append(
                    SmappeeConfigPropertyNumber(
                        coordinator, client, entry.title, device_id, "led",
                        property_name="etc.smart.device.type.car.charger.led.config.brightness",
                        translation_key="led_brightness",
                        value_type="Integer",
                        unit=PERCENTAGE,
                        icon="mdi:led-on",
                        step=10.0,
                        default_min=0.0, default_max=100.0
                    )
                )

    if entities:
        async_add_entities(entities)


class SmappeeConfigPropertyNumber(SmappeeBaseEntity, NumberEntity):
    """Generic entity to handle any standard Smappee v10 configurationProperty slider."""

    _attr_mode = NumberMode.SLIDER
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, coordinator, client, entry_title, device_id, device_type,
        property_name: str, translation_key: str, value_type: str,
        unit: str = None, device_class: NumberDeviceClass = None, icon: str = None,
        step: float = 1.0, default_min: float = 0.0, default_max: float = 100.0
    ):
        super().__init__(coordinator, client, entry_title, device_id=device_id, device_type=device_type, platform_domain="number")
        self._property_name = property_name
        self._value_type = value_type
        self._default_min = default_min
        self._default_max = default_max
        
        self._attr_translation_key = translation_key
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_native_step = step
        if icon:
            self._attr_icon = icon

    @property
    def unique_id(self):
        return f"{self.device_id}_{self.translation_key}_number"

    def _get_property_spec_and_values(self) -> tuple[dict, list] | tuple[None, None]:
        """Dynamically find the spec and active value map from cached metadata."""
        if self.coordinator.data and "smart_devices" in self.coordinator.data:
            for device in self.coordinator.data["smart_devices"]:
                if device.get("id") == self.device_id:
                    for prop in device.get("configurationProperties", []):
                        spec = prop.get("spec", {}) if "spec" in prop else prop
                        if spec.get("name") == self._property_name:
                            return spec, prop.get("values", [])
        return None, None

    @property
    def native_value(self) -> float | None:
        _, values = self._get_property_spec_and_values()
        if values and self._value_type in values[0]:
            target = values[0][self._value_type]
            return float(target.get("value", target) if isinstance(target, dict) else target)
        return self._default_min

    @property
    def native_min_value(self) -> float:
        spec, _ = self._get_property_spec_and_values()
        if spec and "possibleValues" in spec:
            from_data = spec["possibleValues"].get("range", {}).get("from", {}).get(self._value_type, {})
            if from_data:
                return float(from_data.get("value", from_data) if isinstance(from_data, dict) else from_data)
        return self._default_min

    @property
    def native_max_value(self) -> float:
        spec, _ = self._get_property_spec_and_values()
        if spec and "possibleValues" in spec:
            to_data = spec["possibleValues"].get("range", {}).get("to", {}).get(self._value_type, {})
            if to_data:
                return float(to_data.get("value", to_data) if isinstance(to_data, dict) else to_data)
        return self._default_max

    async def async_set_native_value(self, value: float) -> None:
        """Dynamically structure payload configurations and dispatch changes to the backend client."""
        int_value = int(value)
        _LOGGER.info("Updating configuration parameter '%s' to %s", self._property_name, int_value)
        
        value_payload = {"value": int_value} if self._value_type == "Quantity" else int_value
        
        # Use the generic config implementation we established inside client.py earlier
        success = await self.client.update_configuration_property(
            device_id=self.device_id,
            property_name=self._property_name,
            value_dict={self._value_type: value_payload}
        )
        
        if success and self.coordinator.data:
            # Inject optimistic UI update inside local caches
            for device in self.coordinator.data["smart_devices"]:
                if device.get("id") == self.device_id:
                    for prop in device.get("configurationProperties", []):
                        spec = prop.get("spec", {}) if "spec" in prop else prop
                        if spec.get("name") == self._property_name:
                            prop["values"] = [{self._value_type: value_payload}]
                            break
            self.coordinator.async_set_updated_data(self.coordinator.data)
            await asyncio.sleep(1.0)
            await self.coordinator.async_refresh_rendering()


class SmappeeOfflineFailsafeNumber(SmappeeBaseEntity, NumberEntity):
    """Custom unique slider targeting multi-version v11 / v10 fallback configuration endpoints."""

    _attr_translation_key = "offline_failsafe_setting"
    _attr_device_class = NumberDeviceClass.CURRENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_native_step = 1.0
    _attr_native_min_value = 0.0
    _attr_native_max_value = 6.0
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator, client, entry_title, device_id):
        super().__init__(coordinator, client, entry_title, device_id=device_id, device_type="charger", platform_domain="number")

    @property
    def unique_id(self):
        return f"{self.device_id}_offline_failsafe_number"

    @property
    def native_value(self) -> float:
        if self.coordinator.data and "charging_station_details" in self.coordinator.data:
            serial = getattr(self.client, "charging_station_serial", None)
            station_data = self.coordinator.data["charging_station_details"].get(serial) if serial else None
            if station_data and "offlineCharging" in station_data:
                return float(station_data["offlineCharging"].get("failSafe", 3.0))

        for prop in self.smart_device_data.get("configurationProperties", []) if self.smart_device_data else []:
            spec = prop.get("spec", {}) if "spec" in prop else prop
            if spec.get("name") == "etc.smart.device.type.car.charger.config.max.gridassistanceamps":
                values = prop.get("values", [{}])
                if values:
                    return float(values[0].get("Integer", 3.0))
        return 3.0

    async def async_set_native_value(self, value: float) -> None:
        serial = getattr(self.client, "charging_station_serial", None)
        is_enabled = True
        
        if self.coordinator.data and "charging_station_details" in self.coordinator.data and serial:
            station_data = self.coordinator.data["charging_station_details"].get(serial)
            if station_data and "offlineCharging" in station_data:
                is_enabled = bool(station_data["offlineCharging"].get("enabled", True))

        success = await self.client.set_offline_charging_config(is_enabled, int(value))
        
        if success and self.coordinator.data:
            if serial and "charging_station_details" in self.coordinator.data:
                station_data = self.coordinator.data["charging_station_details"].get(serial)
                if station_data:
                    if "offlineCharging" not in station_data:
                        station_data["offlineCharging"] = {}
                    station_data["offlineCharging"]["failSafe"] = int(value)
            
            self.coordinator.async_set_updated_data(self.coordinator.data)
            await asyncio.sleep(1.0)
            await self.coordinator.async_refresh_rendering()


class SmappeeChargePercentageSlider(SmappeeBaseEntity, NumberEntity):
    """Custom action slider executing API dynamic endpoint tasks outside configurations."""

    _attr_translation_key = "charge_percentage_limit"
    _attr_native_min_value = 0.0
    _attr_native_max_value = 100.0
    _attr_native_step = 5.0
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:speedometer"

    def __init__(self, coordinator, client, entry_title, device_id):
        super().__init__(coordinator, client, entry_title, device_id=device_id, device_type="charger", platform_domain="number")

    @property
    def unique_id(self):
        return f"{self.device_id}_charge_percentage_limit_number"

    @property
    def native_value(self) -> float:
        if self.coordinator.data and "charging_station_details" in self.coordinator.data:
            serial = getattr(self.client, "charging_station_serial", None)
            station_data = self.coordinator.data["charging_station_details"].get(serial) if serial else None
            if station_data:
                for module in station_data.get("modules", []):
                    if "carCharger" in module and module["carCharger"]:
                        return float(module["carCharger"].get("percentageLimit", 100.0))
        return 100.0

    async def async_set_native_value(self, value: float) -> None:
        int_value = int(value)
        
        payload = [
            {
                "spec": {"name": "percentageLimit", "species": "Integer", "unit": "%", "required": True},
                "values": [{"Integer": int_value}]
            }
        ]
        
        success = await self.client.execute_device_action(
            device_id=self.device_id,
            action_name="setPercentageLimit",
            payload=payload
        )
        
        if success and self.coordinator.data:
            serial = getattr(self.client, "charging_station_serial", None)
            if serial and "charging_station_details" in self.coordinator.data:
                station_data = self.coordinator.data["charging_station_details"].get(serial)
                if station_data:
                    for module in station_data.get("modules", []):
                        if "carCharger" in module and module["carCharger"]:
                            module["carCharger"]["percentageLimit"] = int_value
            
            self.coordinator.async_set_updated_data(self.coordinator.data)
            await asyncio.sleep(1.0)
            await self.coordinator.async_refresh_rendering()