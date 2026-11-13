"""Tariff sensors with local minute ticks and hourly network updates."""

from datetime import UTC, datetime, timedelta

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .hdo import HdoError, tariff_state


class HdoSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, ean, kind, secondary=False):
        super().__init__(coordinator)
        self.ean, self.kind = ean, kind
        self.secondary = secondary
        self.signal_key = f"{ean}_secondary" if secondary else ean
        self.settings = coordinator.signal_settings[self.signal_key]
        self._attr_unique_id = f"{ean}_hdo_secondary_{kind}" if secondary else f"{ean}_hdo_{kind}"
        self._attr_name = {
            "tariff": "Aktuální tarif",
            "price": "Cena odběru",
            "next_change": "Příští změna tarifu",
        }[kind]
        if kind != "price" and self.settings.get("name"):
            label = "Stav programu" if secondary and kind == "tariff" else self._attr_name
            self._attr_name = f"{self.settings['name']} {label}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, ean)},
            name=f"EG.D {ean}",
            manufacturer="EG.D",
            model="Distribuce24 OpenAPI",
        )
        if kind == "price":
            self._attr_native_unit_of_measurement = "CZK/kWh"
            self._attr_icon = "mdi:cash"
        elif kind == "next_change":
            self._attr_device_class = SensorDeviceClass.TIMESTAMP

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(async_track_time_interval(self.hass, self._tick, timedelta(minutes=1)))

    @callback
    def _tick(self, now):
        self.async_write_ha_state()

    def _state(self):
        data = (self.coordinator.data or {}).get(self.signal_key)
        now = datetime.now(UTC)
        if not data or now - data["fetched_at"] > timedelta(hours=2):
            return None
        try:
            return tariff_state(data["records"], now)
        except HdoError:
            return None

    @property
    def available(self):
        return super().available and self._state() is not None

    @property
    def native_value(self):
        state = self._state()
        if state is None:
            return None
        if self.kind == "price":
            return self.settings["price_" + state["tariff"].lower()]
        if self.secondary and self.kind == "tariff":
            return "Aktivní" if state["tariff"] == "NT" else "Neaktivní"
        return state[self.kind]

    @property
    def extra_state_attributes(self):
        state = self._state()
        settings = self.settings
        return {
            "hdo_code": settings["hdo_code"],
            **(
                {"price_nt": settings["price_nt"], "price_vt": settings["price_vt"]}
                if not self.secondary
                else {}
            ),
            "schedule": settings.get("schedule"),
            "used_for_billing": not self.secondary,
            "program_active": state["tariff"] == "NT" if state else None,
            "nt_today": state["nt_today"] if state else [],
            "schedule_based": True,
        }
