"""Gateway sensor entities."""
from homeassistant.components.sensor import SensorEntity, SensorEntityDescription, SensorStateClass, SensorDeviceClass
from homeassistant.const import EntityCategory, UnitOfTime

from .const import DOMAIN
from .entity import VerizonEntity

SENSORS = (
    SensorEntityDescription(key="ipv4_status", name="IPv4 status", icon="mdi:access-point-network"),
    SensorEntityDescription(key="wan_ipv4", name="WAN IPv4", icon="mdi:ip-network"),
    SensorEntityDescription(key="technology", name="Cellular technology", icon="mdi:radio-tower"),
    SensorEntityDescription(key="signal_5g", name="5G signal strength", icon="mdi:signal",
                            state_class=SensorStateClass.MEASUREMENT),
    SensorEntityDescription(key="signal_4g", name="4G LTE signal strength", icon="mdi:signal",
                            state_class=SensorStateClass.MEASUREMENT, entity_registry_enabled_default=False),
    SensorEntityDescription(key="sim_status", name="SIM status", icon="mdi:sim",
                            entity_category=EntityCategory.DIAGNOSTIC),
    SensorEntityDescription(key="firmware", name="Firmware", icon="mdi:chip",
                            entity_category=EntityCategory.DIAGNOSTIC),
    SensorEntityDescription(key="modem_firmware", name="Modem firmware", icon="mdi:chip",
                            entity_category=EntityCategory.DIAGNOSTIC, entity_registry_enabled_default=False),
    SensorEntityDescription(key="uptime", name="Uptime", device_class=SensorDeviceClass.DURATION,
                            native_unit_of_measurement=UnitOfTime.SECONDS,
                            state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC),
    SensorEntityDescription(key="wan_ipv6", name="WAN IPv6", icon="mdi:ip-network",
                            entity_registry_enabled_default=False),
    SensorEntityDescription(key="wan_type", name="WAN type", icon="mdi:wan",
                            entity_category=EntityCategory.DIAGNOSTIC, entity_registry_enabled_default=False),
    SensorEntityDescription(key="led_status", name="LED status", icon="mdi:led-on",
                            entity_category=EntityCategory.DIAGNOSTIC, entity_registry_enabled_default=False),
    SensorEntityDescription(key="roaming", name="Roaming status", icon="mdi:radio-tower",
                            entity_registry_enabled_default=False),
)


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(VerizonSensor(coordinator, description) for description in SENSORS)


class VerizonSensor(VerizonEntity, SensorEntity):
    def __init__(self, coordinator, description):
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self):
        return self.coordinator.data.get(self.entity_description.key)

