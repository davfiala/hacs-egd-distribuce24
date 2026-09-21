"""Latest readings and synchronization diagnostics; energy uses external statistics."""

from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory, UnitOfEnergy
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import VALID_STATUSES
from .const import DOMAIN


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
        async_add_entities(entities)

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))


class EgdSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, key, kind):
        super().__init__(coordinator)
        self.key, self.kind = key, kind
        stream = coordinator.data["streams"][key]
        self._attr_unique_id = f"{key}_{kind}"
        self._attr_name = (
            f"{stream['profile']} "
            + {
                "energy": "Poslední čtvrthodina",
                "measurement_time": "Čas měření",
                "last_sync": "Poslední synchronizace",
            }[kind]
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, stream["ean"])},
            manufacturer="EG.D",
            name=f"EG.D {stream['ean']}",
            model="Distribuce24 OpenAPI",
        )
        if kind == "energy":
            self._attr_device_class = SensorDeviceClass.ENERGY
            self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
            # No state_class: delayed interval energy must not be counted at polling time.
        else:
            self._attr_device_class = SensorDeviceClass.TIMESTAMP
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
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
        return {
            "profile": stream["profile"],
            "statistic_id": f"{DOMAIN}:{self.key}",
            "measurement_status": (stream["latest"] or {}).get("status"),
        }
