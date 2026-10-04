"""Gateway-reported IPv4 connection state, separate from Internet reachability."""
from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass

from .const import DOMAIN
from .entity import VerizonEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([VerizonConnected(hass.data[DOMAIN][entry.entry_id])])


class VerizonConnected(VerizonEntity, BinarySensorEntity):
    _attr_name = "IPv4 connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator):
        super().__init__(coordinator, "ipv4_connected")

    @property
    def is_on(self):
        return self.coordinator.data["ipv4_status"] == "Connected"

