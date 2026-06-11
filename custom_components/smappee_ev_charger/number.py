"""Set up and manage Smappee Charger number entities."""

import asyncio
from contextlib import suppress
import json
import logging
from typing import Any

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfElectricCurrent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .sensor import SmappeeBaseEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Smappee number entities dynamically based on discovered devices."""
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
                _LOGGER.debug(
                    "Creating dynamic configuration numbers for Smappee charger: %s",
                    device_id,
                )
                entities.extend(
                    [
                        SmappeeMaxCurrentLimitNumber(
                            coordinator, client, entry.title, device_id
                        ),
                        SmappeeConfigPropertyNumber(
                            coordinator,
                            client,
                            entry.title,
                            device_id,
                            "charger",
                            property_name="etc.smart.device.type.car.charger.config.min.excesspct",
                            translation_key="min_excess_percentage",
                            value_type="Integer",
                            unit=PERCENTAGE,
                            icon="mdi:sun-wireless",
                            default_min=0.0,
                            default_max=100.0,
                        ),
                        SmappeeOfflineFailsafeNumber(
                            coordinator, client, entry.title, device_id
                        ),
                        SmappeeChargePercentageSlider(
                            coordinator, client, entry.title, device_id
                        ),
                    ]
                )

            elif category == "LED":
                _LOGGER.debug(
                    "Creating dynamic LED brightness configuration slider for: %s",
                    device_id,
                )
                entities.append(
                    SmappeeConfigPropertyNumber(
                        coordinator,
                        client,
                        entry.title,
                        device_id,
                        "led",
                        property_name="etc.smart.device.type.car.charger.led.config.brightness",
                        translation_key="led_brightness",
                        value_type="Integer",
                        unit=PERCENTAGE,
                        icon="mdi:led-on",
                        step=10.0,
                        default_min=0.0,
                        default_max=100.0,
                    )
                )

    if entities:
        async_add_entities(entities)


class SmappeeConfigPropertyNumber(SmappeeBaseEntity, NumberEntity):
    """Handle standard generic Smappee v10 configurationProperty adjustments."""

    _attr_mode = NumberMode.SLIDER
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator,
        client,
        entry_title,
        device_id: str,
        device_type: str,
        property_name: str,
        translation_key: str,
        value_type: str,
        unit: str = None,
        device_class: NumberDeviceClass = None,
        icon: str = None,
        step: float = 1.0,
        default_min: float = 0.0,
        default_max: float = 100.0,
    ) -> None:
        """Initialize the generic configuration property number entity."""
        super().__init__(
            coordinator,
            client,
            entry_title,
            device_id=device_id,
            device_type=device_type,
            platform_domain="number",
        )
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
    def unique_id(self) -> str:
        """Return a unique ID for this number entity."""
        return f"{self.device_id}_{self._attr_translation_key}_number"

    def _get_property_spec_and_values(self) -> tuple[dict, list] | tuple[None, None]:
        """Find the property description spec and active value maps from cached registries."""
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
        """Return the current active configuration property value."""
        _, values = self._get_property_spec_and_values()
        if values and self._value_type in values[0]:
            target = values[0][self._value_type]
            return float(
                target.get("value", target) if isinstance(target, dict) else target
            )
        return self._default_min

    @property
    def native_min_value(self) -> float:
        """Return the minimum boundary requirement parsed from specifications."""
        spec, _ = self._get_property_spec_and_values()
        if spec and "possibleValues" in spec:
            from_data = (
                spec["possibleValues"]
                .get("range", {})
                .get("from", {})
                .get(self._value_type, {})
            )
            if from_data:
                return float(
                    from_data.get("value", from_data)
                    if isinstance(from_data, dict)
                    else from_data
                )
        return self._default_min

    @property
    def native_max_value(self) -> float:
        """Return the maximum boundary constraint parsed from specifications."""
        spec, _ = self._get_property_spec_and_values()
        if spec and "possibleValues" in spec:
            to_data = (
                spec["possibleValues"]
                .get("range", {})
                .get("to", {})
                .get(self._value_type, {})
            )
            if to_data:
                return float(
                    to_data.get("value", to_data)
                    if isinstance(to_data, dict)
                    else to_data
                )
        return self._default_max

    async def async_set_native_value(self, value: float) -> None:
        """Transmit updated target values to the corresponding smart device property configuration."""
        int_value = int(value)
        _LOGGER.info(
            "Updating configuration parameter '%s' to %s",
            self._property_name,
            int_value,
        )

        value_payload = (
            {"value": int_value} if self._value_type == "Quantity" else int_value
        )
        service_location_id = self.smart_device_data.get("serviceLocation")

        success = await self.client.update_configuration_property(
            device_id=self.device_id,
            property_name=self._property_name,
            value_dict={self._value_type: value_payload},
            service_location_id=service_location_id,
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
            await self.coordinator.async_request_refresh()


class SmappeeOfflineFailsafeNumber(SmappeeBaseEntity, NumberEntity):
    """Manage fallback safe operating limits used during offline processing disruptions."""

    _attr_translation_key = "offline_failsafe_setting"
    _attr_device_class = NumberDeviceClass.CURRENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_native_step = 1.0
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator, client, entry_title, device_id: str) -> None:
        """Initialize the offline failsafe number entity."""
        super().__init__(
            coordinator,
            client,
            entry_title,
            device_id=device_id,
            device_type="charger",
            platform_domain="number",
        )

    @property
    def unique_id(self) -> str:
        """Return a unique ID for this number entity."""
        return f"{self.device_id}_offline_failsafe_number"

    @property
    def native_min_value(self) -> float:
        """Return the minimum charging current requirement boundary constraint."""
        return 6.0

    @property
    def native_max_value(self) -> float:
        """Return the maximum allowed ceiling current extracted dynamically from configurations."""
        if self.coordinator.data and "smart_devices" in self.coordinator.data:
            for device in self.coordinator.data["smart_devices"]:
                if device.get("id") == self.device_id:
                    for prop in device.get("configurationProperties", []):
                        spec = prop.get("spec", {}) if "spec" in prop else prop
                        if (
                            spec.get("name")
                            == "etc.smart.device.type.car.charger.config.max.current"
                        ):
                            values = prop.get("values", [])
                            if values and "Quantity" in values[0]:
                                return float(values[0]["Quantity"].get("value", 32.0))
        return 32.0

    @property
    def native_value(self) -> float:
        """Return the current offline safety backup constraint value."""
        if (
            self.coordinator.data
            and "charging_station_details" in self.coordinator.data
        ):
            serial = getattr(self.client, "charging_station_serial", None)
            station_data = (
                self.coordinator.data["charging_station_details"].get(str(serial))
                if serial
                else None
            )
            if station_data and "offlineCharging" in station_data:
                return float(station_data["offlineCharging"].get("failSafe", 3.0))

        for prop in (
            self.smart_device_data.get("configurationProperties", [])
            if self.smart_device_data
            else []
        ):
            spec = prop.get("spec", {}) if "spec" in prop else prop
            if (
                spec.get("name")
                == "etc.smart.device.type.car.charger.config.max.gridassistanceamps"
            ):
                values = prop.get("values", [{}])
                if values:
                    return float(values[0].get("Integer", 3.0))
        return 3.0

    async def async_set_native_value(self, value: float) -> None:
        """Transmit updated safety boundaries down to the localized persistent storage parameters."""
        serial = getattr(self.client, "charging_station_serial", None)
        is_enabled = False

        if (
            self.coordinator.data
            and "charging_station_details" in self.coordinator.data
            and serial
        ):
            station_data = self.coordinator.data["charging_station_details"].get(
                str(serial)
            )
            if station_data and "offlineCharging" in station_data:
                is_enabled = bool(station_data["offlineCharging"].get("enabled", False))

        # STRICT GUARD CLAUSE: Abort execution if offline charging is disabled
        if not is_enabled:
            _LOGGER.warning(
                "Aborted failsafe current adjustment for charger %s: offline charging is currently disabled (False)",
                self.device_id,
            )
            return

        success = await self.client.set_offline_charging_config(is_enabled, int(value))

        if success and self.coordinator.data:
            if serial and "charging_station_details" in self.coordinator.data:
                station_data = self.coordinator.data["charging_station_details"].get(
                    str(serial)
                )
                if station_data:
                    if "offlineCharging" not in station_data:
                        station_data["offlineCharging"] = {}
                    station_data["offlineCharging"]["failSafe"] = int(value)

            self.coordinator.async_set_updated_data(self.coordinator.data)
            await asyncio.sleep(1.0)
            await self.coordinator.async_request_refresh()


class SmappeeChargePercentageSlider(SmappeeBaseEntity, NumberEntity):
    """Throttle total output processing velocity limits matching instantaneous tasks."""

    _attr_translation_key = "charge_percentage_limit"
    _attr_native_min_value = 0.0
    _attr_native_max_value = 100.0
    _attr_native_step = 5.0
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:speedometer"

    def __init__(self, coordinator, client, entry_title, device_id: str) -> None:
        """Initialize the charge percentage limit entity."""
        super().__init__(
            coordinator,
            client,
            entry_title,
            device_id=device_id,
            device_type="charger",
            platform_domain="number",
        )
        self.mapped_location_id = str(coordinator.config_entry.data.get("station_id"))

    @property
    def unique_id(self) -> str:
        """Return a unique ID for this number entity."""
        return f"{self.device_id}_charge_percentage_limit_number"

    @property
    def native_value(self) -> float:
        """Return the current dynamic constraint threshold restriction setting."""
        # 1. Primary: Evaluate state updates coming over the active WebSocket stream
        if self.coordinator.data and "mqtt_locations" in self.coordinator.data:
            mqtt_locations = self.coordinator.data["mqtt_locations"]
            location_data = mqtt_locations.get(self.mapped_location_id, {})
            mqtt_state = location_data.get("state")

            if mqtt_state:
                with suppress(Exception):
                    mqtt_json = (
                        mqtt_state
                        if isinstance(mqtt_state, dict)
                        else json.loads(mqtt_state)
                    )
                    if isinstance(mqtt_json, dict) and "percentageLimit" in mqtt_json:
                        return float(mqtt_json["percentageLimit"])

        # 2. Secondary Fallback: Fetch from the v11 REST Cloud API registry
        if (
            self.coordinator.data
            and "charging_station_details" in self.coordinator.data
        ):
            serial = getattr(self.client, "charging_station_serial", None)
            station_data = (
                self.coordinator.data["charging_station_details"].get(str(serial))
                if serial
                else None
            )

            if station_data:
                for module in station_data.get("modules", []):
                    if "carCharger" in module and module["carCharger"]:
                        return float(module["carCharger"].get("percentageLimit", 100.0))

        return 100.0

    async def async_set_native_value(self, value: float) -> None:
        """Dispatch instant action scaling payloads directly to endpoints."""
        int_value = int(value)

        payload = [
            {
                "spec": {
                    "name": "percentageLimit",
                    "species": "Integer",
                    "unit": "%",
                    "required": True,
                },
                "values": [{"Integer": int_value}],
            }
        ]

        success = await self.client.execute_device_action(
            device_id=self.device_id, action_name="setPercentageLimit", payload=payload
        )

        if success and self.coordinator.data:
            serial = getattr(self.client, "charging_station_serial", None)

            # Optimistic update: REST API structure
            if serial and "charging_station_details" in self.coordinator.data:
                station_data = self.coordinator.data["charging_station_details"].get(
                    str(serial)
                )
                if station_data:
                    for module in station_data.get("modules", []):
                        if "carCharger" in module and module["carCharger"]:
                            module["carCharger"]["percentageLimit"] = int_value

            # Optimistic update: Real-time MQTT stream states
            if "mqtt_locations" in self.coordinator.data:
                loc_data = self.coordinator.data["mqtt_locations"].setdefault(
                    self.mapped_location_id, {}
                )
                state_data = loc_data.setdefault("state", {})
                if isinstance(state_data, dict):
                    state_data["percentageLimit"] = int_value

            self.coordinator.async_set_updated_data(self.coordinator.data)
            await asyncio.sleep(1.0)
            await self.coordinator.async_request_refresh()


class SmappeeMaxCurrentLimitNumber(SmappeeBaseEntity, NumberEntity):
    """Manage the upper physical phase current limit via the v11 installation configuration endpoint."""

    _attr_translation_key = "max_current_setting"
    _attr_device_class = NumberDeviceClass.CURRENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_native_step = 1.0
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator, client, entry_title, device_id: str) -> None:
        """Initialize the Smappee max current configuration number entity."""
        super().__init__(
            coordinator,
            client,
            entry_title,
            device_id=device_id,
            device_type="charger",
            platform_domain="number",
        )

    @property
    def unique_id(self) -> str:
        """Return a unique ID for this number entity."""
        return f"{self.device_id}_max_current_limit_config_number"

    def _get_installation_config(self) -> dict[str, Any] | None:
        """Fetch the currently active installation configuration branch from cache."""
        if (
            not self.coordinator.data
            or "charging_station_details" not in self.coordinator.data
        ):
            return None

        details_dict = self.coordinator.data["charging_station_details"]
        serial = getattr(self.client, "charging_station_serial", None)
        station_data = details_dict.get(str(serial)) if serial else None

        if not station_data and len(details_dict) > 0:
            station_data = list(details_dict.values())[0]

        if station_data and "installationConfiguration" in station_data:
            return station_data["installationConfiguration"].get(
                "currentlyConfigured", {}
            )

        return None

    def _get_v10_property_spec(self) -> dict[str, Any] | None:
        """Extract the structural v10 metadata spec tracking current configurations out of cache frameworks."""
        for prop in (
            self.smart_device_data.get("configurationProperties", [])
            if self.smart_device_data
            else []
        ):
            spec = prop.get("spec", {}) if "spec" in prop else prop
            if (
                spec.get("name")
                == "etc.smart.device.type.car.charger.config.max.current"
            ):
                return spec
        return None

    @property
    def native_min_value(self) -> float:
        """Return the minimum charging current requirement boundary parsed from system specifications."""
        spec = self._get_v10_property_spec()
        if spec and "possibleValues" in spec:
            from_data = (
                spec["possibleValues"]
                .get("range", {})
                .get("from", {})
                .get("Quantity", {})
            )
            if from_data:
                return float(from_data.get("value", 6.0))
        return 6.0

    @property
    def native_max_value(self) -> float:
        """Return the maximum allowed target capacity ceiling extracted dynamically from system specifications."""
        spec = self._get_v10_property_spec()
        if spec and "possibleValues" in spec:
            to_data = (
                spec["possibleValues"]
                .get("range", {})
                .get("to", {})
                .get("Quantity", {})
            )
            if to_data:
                return float(to_data.get("value", 32.0))
        return 32.0

    @property
    def native_value(self) -> float | None:
        """Return the active maximum current limit configuration value."""
        config = self._get_installation_config()
        if config:
            max_current_array = config.get("maximumCurrent", [])
            if max_current_array:
                return float(max_current_array[0].get("value", 22.0))
        return 22.0

    async def async_set_native_value(self, value: float) -> None:
        """Construct the structural installation mapping package and dispatch the updated max current limit."""
        int_value = int(value)
        _LOGGER.info(
            "Modifying physical installation max current safety ceiling for charger to: %s A",
            int_value,
        )

        config = self._get_installation_config()
        if not config:
            _LOGGER.error(
                "Aborted current limit configuration: active installation specs missing from cache registries."
            )
            return

        amount_cables = config.get("amountPowerSupplyCables", "ONE")
        phases = config.get("phases", [["PHASEA", "PHASEB", "PHASEC"]])

        payload = {
            "amountPowerSupplyCables": amount_cables,
            "maximumCurrent": [{"value": int_value, "unit": "AMPERE"}],
            "phases": phases,
        }

        success = await self.client.set_installation_configuration(payload)

        if success and self.coordinator.data:
            config["maximumCurrent"] = [{"value": int_value, "unit": "AMPERE"}]
            self.coordinator.async_set_updated_data(self.coordinator.data)

            await asyncio.sleep(1.5)
            await self.coordinator.async_request_refresh()
