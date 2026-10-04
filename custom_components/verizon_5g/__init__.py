"""Local Verizon 5G gateway monitoring."""
from homeassistant.const import CONF_HOST, CONF_PASSWORD, EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry

from .api import GatewayClient
from .const import DOMAIN, CONF_VERIFY_SSL
from .coordinator import VerizonCoordinator

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    client = GatewayClient(entry.data[CONF_HOST], entry.data[CONF_PASSWORD],
                           entry.data.get(CONF_VERIFY_SSL, False))
    coordinator = VerizonCoordinator(hass, entry, client)
    try:
        await coordinator.async_config_entry_first_refresh()
        hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        await client.async_close()
        raise
    async def close_on_stop(event):
        await client.async_close()

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, close_on_stop))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    coordinator = hass.data[DOMAIN].pop(entry.entry_id)
    await coordinator.client.async_close()
    return True
