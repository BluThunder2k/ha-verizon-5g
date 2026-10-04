"""A single polling coordinator for all entities."""
from datetime import timedelta
import logging

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import GatewayAuthError, GatewayError
from .const import CONF_INTERVAL, DEFAULT_INTERVAL

LOGGER = logging.getLogger(__name__)


class VerizonCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry, client):
        super().__init__(hass, LOGGER, name=entry.title, config_entry=entry,
                         update_interval=timedelta(seconds=entry.data.get(CONF_INTERVAL, DEFAULT_INTERVAL)),
                         always_update=False)
        self.client = client

    async def _async_update_data(self):
        try:
            data = await self.client.async_fetch()
            if self.config_entry.unique_id and data["mac"] != self.config_entry.unique_id:
                raise UpdateFailed("A different gateway is responding at the configured address")
            return data
        except GatewayAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from None
        except GatewayError as err:
            raise UpdateFailed(str(err)) from None

