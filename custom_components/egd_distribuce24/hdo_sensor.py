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

    def __init__(self, coordinator, ean, kind):
        super().__init__(coordinator)
        self.ean, self.kind = ean, kind
        self._attr_unique_id = f"{ean}_hdo_{kind}"
        self._attr_name = {
            "tariff": "Aktuální tarif",
            "price": "Cena odběru",
            "next_change": "Příští změna tarifu",
        }[kind]
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
        data = (self.coordinator.data or {}).get(self.ean)
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
            return self.coordinator.settings[self.ean]["price_" + state["tariff"].lower()]
        return state[self.kind]

    @property
    def extra_state_attributes(self):
        state = self._state()
        settings = self.coordinator.settings[self.ean]
        return {
            "hdo_code": settings["hdo_code"],
            "price_nt": settings["price_nt"],
            "price_vt": settings["price_vt"],
            "nt_today": state["nt_today"] if state else [],
            "schedule_based": True,
        }
