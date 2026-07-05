import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .const import DOMAIN
from .api import LantaAPI

class LantaConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input:
            phone = user_input["Phone"]
            session = async_get_clientsession(self.hass)
            api = LantaAPI(phone)

            try:
                await api.request_code(session)
                token = await api.poll_for_token(session)
                doors = await api.get_doors(session)
                return self.async_create_entry(
                    title=phone,
                    data={"phone": phone, "token": token, "doors": doors}
                )
            except TimeoutError:
                errors["base"] = "timeout"
            except Exception:
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required("Phone"): str
            }),
            errors=errors,
            description_placeholders={}
        )