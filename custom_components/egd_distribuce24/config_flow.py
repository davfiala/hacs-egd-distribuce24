"""UI setup and credential renewal."""

import hashlib

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import ApiError, AuthenticationError, EgdClient
from .const import CONF_CLIENT_ID, CONF_CLIENT_SECRET, CONF_EXPORT, DOMAIN, PROFILES


class EgdConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_meters(self, user_input=None):
        errors = {}
        if user_input is not None:
            selected = set(user_input.get("meters", []))
            available = {m["ean"] for m in self._meters}
            if not selected or not selected <= available:
                errors["base"] = "select_meters"
            else:
                identity = hashlib.sha256(",".join(sorted(selected)).encode()).hexdigest()
                await self.async_set_unique_id(identity)
                self._abort_if_unique_id_configured()
                if any(
                    selected.intersection(e.data.get("meters", []))
                    for e in self._async_current_entries()
                ):
                    return self.async_abort(reason="already_configured")
                return self.async_create_entry(
                    title="EG.D Distribuce24",
                    data={
                        **self._credentials,
                        "meters": sorted(selected),
                    },
                )
        return self.async_show_form(
            step_id="meters",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "meters", default=[m["ean"] for m in self._meters]
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                {"value": m["ean"], "label": f"{m['ean']} ({m['typMereni']})"}
                                for m in self._meters
                            ],
                            multiple=True,
                            mode=SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
        )

    async def async_step_user(self, user_input=None):
        return await self._form(user_input, "user")

    async def async_step_reauth(self, entry_data):
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        return await self._form(user_input, "reauth_confirm")

    async def _form(self, user_input, step):
        errors = {}
        if user_input is not None:
            user_input = dict(user_input)
            for key in (CONF_CLIENT_ID, CONF_CLIENT_SECRET):
                user_input[key] = user_input[key].strip()
            try:
                client = EgdClient(
                    async_get_clientsession(self.hass),
                    user_input[CONF_CLIENT_ID],
                    user_input[CONF_CLIENT_SECRET],
                )
                meters = [m for m in await client.meters() if m["typMereni"] in PROFILES]
                if not meters:
                    return self.async_abort(reason="no_supported_meters")
            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except ApiError:
                errors["base"] = "cannot_connect"
            else:
                if step == "reauth_confirm":
                    entry = self._get_reauth_entry()
                    if not set(entry.data["meters"]) <= {m["ean"] for m in meters}:
                        return self.async_abort(reason="missing_selected_meters")
                    return self.async_update_reload_and_abort(entry, data_updates=user_input)
                self._credentials, self._meters = user_input, meters
                return await self.async_step_meters()
        schema = {
            vol.Required(CONF_CLIENT_ID): str,
            vol.Required(CONF_CLIENT_SECRET): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD)
            ),
        }
        if step == "user":
            schema[vol.Optional(CONF_EXPORT, default=False)] = bool
        return self.async_show_form(step_id=step, data_schema=vol.Schema(schema), errors=errors)
