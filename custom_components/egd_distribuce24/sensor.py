"""Latest readings and synchronization diagnostics; energy uses external statistics."""

from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory, UnitOfEnergy
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import VALID_STATUSES
from .const import DOMAIN, profile_name
from .hdo_sensor import HdoSensor


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    known = set()

    def add_new():
        entities = []
        for key in coordinator.data["streams"]:
            if key not in known:
                known.add(key)
                entities.extend(
                    EgdSensor(coordinator, key, kind)
                    for kind in ("energy", "measurement_time", "last_sync")
                )
                stream = coordinator.data["streams"][key]
                if "cost_ledger" in stream:
                    entities.append(EgdSensor(coordinator, key, "monthly_fee"))
        async_add_entities(entities)

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))
    if coordinator.hdo is not None:
        async_add_entities(
            HdoSensor(coordinator.hdo, ean, kind)
            for ean in coordinator.hdo.settings
            for kind in ("tariff", "price", "next_change")
        )


class EgdSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, key, kind):
        super().__init__(coordinator)
        self.key, self.kind = key, kind
        stream = coordinator.data["streams"][key]
        self._attr_unique_id = f"{key}_{kind}"
        self._attr_name = (
            f"{profile_name(coordinator.entry.data, stream['profile'])} "
            + {
                "energy": "Poslední čtvrthodina",
                "measurement_time": "Čas měření",
                "last_sync": "Poslední synchronizace",
                "monthly_fee": "Stálá měsíční platba",
            }[kind]
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, stream["ean"])},
            manufacturer="EG.D",
            name=f"EG.D {stream['ean']}",
            model="Distribuce24 OpenAPI",
        )
        if kind == "monthly_fee":
            self._attr_device_class = SensorDeviceClass.MONETARY
            self._attr_native_unit_of_measurement = "CZK"
        elif kind == "energy":
            self._attr_device_class = SensorDeviceClass.ENERGY
            self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
            # No state_class: delayed interval energy must not be counted at polling time.
        else:
            self._attr_device_class = SensorDeviceClass.TIMESTAMP
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        if self.kind == "monthly_fee":
            stream = self.coordinator.data["streams"][self.key]
            return float(self.coordinator.entry.data["cost_settings"][stream["ean"]]["monthly_fee"])
        latest = self.coordinator.data["streams"][self.key]["latest"]
        if self.kind == "last_sync":
            value = self.coordinator.data["last_sync"]
            return datetime.fromisoformat(value) if value else None
        if not latest:
            return None
        if self.kind == "measurement_time":
            return datetime.fromisoformat(latest["timestamp"])
        return latest["value"] if latest["status"] in VALID_STATUSES else None

    @property
    def extra_state_attributes(self):
        stream = self.coordinator.data["streams"][self.key]
        if self.kind == "monthly_fee":
            return {
                "period": "month",
                "priced_hours": stream.get("priced_hours", 0),
                "archived_energy_hours": len(stream["hours"]),
                **{
                    kind + "_statistic_id": f"{DOMAIN}:{self.key}_{kind}"
                    for kind in ("energy_cost", "standing_cost", "total_cost")
                },
            }
        return {
            "profile": stream["profile"],
            "statistic_id": f"{DOMAIN}:{self.key}",
            "measurement_status": (stream["latest"] or {}).get("status"),
        }
