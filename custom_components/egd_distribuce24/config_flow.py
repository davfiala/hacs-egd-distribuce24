"""UI setup and credential renewal."""

import hashlib
from datetime import UTC, datetime

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
from .const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_EXPORT,
    CONF_EXPORT_NAME,
    CONF_IMPORT_NAME,
    DOMAIN,
    PROFILES,
)
from .costs import validate_cost_settings
from .hdo import (
    HdoClient,
    HdoError,
    available_plans,
    normalize_settings,
    select_records,
    tariff_state,
)


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
                self._selected = sorted(selected)
                self._hdo_settings = {}
                self._cost_settings = {}
                self._hdo_index = 0
                self._hdo_catalog = None
                return await self.async_step_hdo()
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

    async def async_step_reconfigure(self, user_input=None):
        entry = self._get_reconfigure_entry()
        self._selected = list(entry.data["meters"])
        self._hdo_settings = dict(entry.data.get("hdo_settings", {}))
        self._cost_settings = dict(entry.data.get("cost_settings", {}))
        self._hdo_index = 0
        self._hdo_catalog = None
        return await self.async_step_hdo()

    async def async_step_hdo(self, user_input=None):
        ean = self._selected[self._hdo_index]
        saved = self._hdo_settings.get(ean, {})
        values = user_input if user_input is not None else saved
        errors = {}
        choices = []
        if user_input is not None:
            if user_input.get("enable_hdo", False):
                try:
                    settings = normalize_settings(user_input)
                    if self._hdo_catalog is None:
                        self._hdo_catalog = await HdoClient(
                            async_get_clientsession(self.hass)
                        ).catalog()
                    records = select_records(*self._hdo_catalog, settings)
                    choices = available_plans(records)
                    if len(choices) > 1 and user_input.get("schedule") not in choices:
                        raise HdoError("select_hdo_schedule")
                    settings["schedule"] = (
                        user_input.get("schedule") if len(choices) > 1 else choices[0]
                    )
                    records = select_records(*self._hdo_catalog, settings)
                    tariff_state(records, datetime.now(UTC))
                    self._hdo_settings[ean] = settings
                except HdoError as err:
                    errors["base"] = str(err)
            else:
                self._hdo_settings.pop(ean, None)
            if not errors:
                return await self.async_step_costs()
        schema = {vol.Optional("enable_hdo", default=values.get("enable_hdo", bool(saved))): bool}
        for key in ("postcode", "hdo_code", "price_nt", "price_vt"):
            schema[vol.Optional(key, default=str(values.get(key, "")))] = str
        if len(choices) > 1:
            default = values.get("schedule")
            if default not in choices:
                default = next((name for name in choices if "relé1" in name), choices[0])
            schema[vol.Required("schedule", default=default)] = SelectSelector(
                SelectSelectorConfig(options=choices, mode=SelectSelectorMode.DROPDOWN)
            )
        return self.async_show_form(
            step_id="hdo",
            data_schema=vol.Schema(schema),
            errors=errors,
            description_placeholders={"ean": ean},
        )

    async def async_step_costs(self, user_input=None):
        ean = self._selected[self._hdo_index]
        saved = self._cost_settings.get(ean, {})
        values = user_input if user_input is not None else saved
        errors = {}
        if user_input is not None:
            try:
                self._cost_settings[ean] = validate_cost_settings(user_input, datetime.now(UTC))
            except ValueError as err:
                errors["base"] = str(err)
            else:
                self._hdo_index += 1
                if self._hdo_index < len(self._selected):
                    return await self.async_step_hdo()
                data = {"hdo_settings": self._hdo_settings, "cost_settings": self._cost_settings}
                if self.source == "reconfigure":
                    return self.async_update_reload_and_abort(
                        self._get_reconfigure_entry(), data_updates=data
                    )
                return self.async_create_entry(
                    title="EG.D Distribuce24",
                    data={**self._credentials, "meters": self._selected, **data},
                )
        return self.async_show_form(
            step_id="costs",
            errors=errors,
            description_placeholders={"ean": ean},
            data_schema=vol.Schema(
                {
                    vol.Required("monthly_fee", default=str(values.get("monthly_fee", "0"))): str,
                    vol.Optional(
                        "backfill_prices", default=values.get("backfill_prices", False)
                    ): bool,
                    vol.Optional("backfill_from", default=values.get("backfill_from") or ""): str,
                }
            ),
        )

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
            schema[vol.Optional(CONF_IMPORT_NAME, default="Odběr ze sítě")] = vol.All(
                str, vol.Length(max=120)
            )
            schema[vol.Optional(CONF_EXPORT_NAME, default="Dodávka do sítě")] = vol.All(
                str, vol.Length(max=120)
            )
        return self.async_show_form(step_id=step, data_schema=vol.Schema(schema), errors=errors)
