"""Common device identity and coordinated availability."""
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


class VerizonEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, key):
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.data['mac']}_{key}"

    @property
    def device_info(self):
        data = self.coordinator.data
        return DeviceInfo(
            identifiers={(DOMAIN, data["mac"])},
            name=self.coordinator.config_entry.title,
            manufacturer="Askey", model=data["model"],
            sw_version=data.get("firmware"), hw_version=data.get("hardware"),
            configuration_url=self.coordinator.client.base_url,
        )

