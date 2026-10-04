"""UI configuration, reconfiguration and credential recovery."""
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PASSWORD
from homeassistant.helpers import selector

from .api import (GatewayClient, GatewayAuthError, GatewayBusyError, GatewayError,
                  UnsupportedGatewayError, normalize_host)
from .const import (DOMAIN, CONF_INTERVAL, CONF_VERIFY_SSL, DEFAULT_HOST,
                    DEFAULT_NAME, DEFAULT_INTERVAL)


def schema(defaults=None):
    defaults = defaults or {}
    return vol.Schema({
        vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, DEFAULT_NAME)): str,
        vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, DEFAULT_HOST)): str,
        vol.Required(CONF_PASSWORD): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
        vol.Required(CONF_INTERVAL, default=defaults.get(CONF_INTERVAL, DEFAULT_INTERVAL)):
            vol.All(vol.Coerce(int), vol.Range(min=30, max=3600)),
        vol.Required(CONF_VERIFY_SSL, default=defaults.get(CONF_VERIFY_SSL, False)): bool,
    })


class VerizonConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def _validate(self, user_input):
        data = dict(user_input)
        data[CONF_HOST] = normalize_host(data[CONF_HOST])
        data[CONF_NAME] = data[CONF_NAME].strip() or DEFAULT_NAME
        client = GatewayClient(data[CONF_HOST], data[CONF_PASSWORD], data[CONF_VERIFY_SSL])
        try:
            snapshot = await client.async_fetch()
            return data, snapshot["mac"]
        finally:
            await client.async_close()

    async def _form(self, step_id, user_input=None, entry=None):
        errors = {}
        if user_input is not None:
            try:
                data, identity = await self._validate(user_input)
            except ValueError:
                errors[CONF_HOST] = "invalid_host"
            except GatewayAuthError:
                errors["base"] = "invalid_auth"
            except GatewayBusyError:
                errors["base"] = "gateway_busy"
            except UnsupportedGatewayError:
                errors["base"] = "unsupported_model"
            except GatewayError:
                errors["base"] = "cannot_connect"
            else:
                if entry:
                    if identity != entry.unique_id:
                        errors["base"] = "wrong_device"
                    else:
                        self.hass.config_entries.async_update_entry(entry, title=data[CONF_NAME])
                        return self.async_update_reload_and_abort(
                            entry, data_updates=data,
                            reason="reauth_successful" if step_id == "reauth_confirm" else "reconfigure_successful")
                else:
                    await self.async_set_unique_id(identity)
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(title=data[CONF_NAME], data=data)
        return self.async_show_form(step_id=step_id,
                                    data_schema=schema(user_input or (entry.data if entry else None)),
                                    errors=errors)

    async def async_step_user(self, user_input=None):
        return await self._form("user", user_input)

    async def async_step_reconfigure(self, user_input=None):
        return await self._form("reconfigure", user_input, self._get_reconfigure_entry())

    async def async_step_reauth(self, entry_data):
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        return await self._form("reauth_confirm", user_input, self._get_reauth_entry())

