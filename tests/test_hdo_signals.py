"""Two relay programs stay isolated; lightweight HA contract doubles only."""

import importlib
import sys
import types
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from test_hdo import record


@pytest.fixture
def modules(monkeypatch):
    class Coordinator:
        def __init__(self, *args, **kwargs):
            self.last_update_success = True

    class Entity:
        def __init__(self, coordinator):
            self.coordinator = coordinator

        @property
        def available(self):
            return self.coordinator.last_update_success

    class Sensor:
        pass

    definitions = {
        "homeassistant.components.sensor": {
            "SensorDeviceClass": types.SimpleNamespace(TIMESTAMP="timestamp"),
            "SensorEntity": Sensor,
        },
        "homeassistant.core": {"callback": lambda f: f},
        "homeassistant.helpers.device_registry": {"DeviceInfo": dict},
        "homeassistant.helpers.event": {"async_track_time_interval": lambda *args: None},
        "homeassistant.helpers.update_coordinator": {
            "CoordinatorEntity": Entity,
            "DataUpdateCoordinator": Coordinator,
            "UpdateFailed": RuntimeError,
        },
    }
    for name, attrs in definitions.items():
        module = types.ModuleType(name)
        module.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, module)
    names = ["egd_client_test.hdo_coordinator", "egd_client_test.hdo_sensor"]
    for name in names:
        sys.modules.pop(name, None)
    coordinator_module, sensor_module = [importlib.import_module(name) for name in names]
    yield coordinator_module, sensor_module
    for name in names:
        sys.modules.pop(name, None)


def setup(module, secondary=True):
    primary = {
        "postcode": "12345",
        "hdo_code": "Example_1",
        "schedule": "Heating",
        "name": "Vytápění",
        "price_nt": 3,
        "price_vt": 6,
    }
    if secondary:
        primary["secondary"] = {
            "postcode": "12345",
            "hdo_code": "Example_1",
            "schedule": "Water",
            "name": "TUV",
        }
    entry = types.SimpleNamespace(data={"hdo_settings": {"ean": primary}})
    coord = module.HdoCoordinator(None, entry, None)
    coord.client.catalog = AsyncMock(
        return_value=(
            [{"PSC": "12345", "Region": "ZAPAD"}],
            [
                record([{"od": "00:00:00", "do": "23:59:00"}], plan="Heating"),
                record([], plan="Water"),
            ],
        )
    )
    return coord


@pytest.mark.asyncio
async def test_two_relays_independent_and_price_uses_primary(modules):
    module, sensors = modules
    coord = setup(module)
    coord.data = await coord._async_update_data()
    assert set(coord.data) == {"ean", "ean_secondary"}
    assert coord.client.catalog.await_count == 1
    primary = sensors.HdoSensor(coord, "ean", "tariff")
    secondary = sensors.HdoSensor(coord, "ean", "tariff", secondary=True)
    assert primary.native_value == "NT"
    assert secondary.native_value == "Neaktivní"
    assert sensors.HdoSensor(coord, "ean", "price").native_value == 3
    assert primary._attr_unique_id == "ean_hdo_tariff"
    assert secondary._attr_unique_id == "ean_hdo_secondary_tariff"
    assert primary._attr_device_info == secondary._attr_device_info
    assert secondary.extra_state_attributes["used_for_billing"] is False
    assert "price_nt" not in secondary.extra_state_attributes
    assert "TUV" in secondary._attr_name


@pytest.mark.asyncio
async def test_bad_secondary_does_not_disable_primary(modules):
    module, sensors = modules
    coord = setup(module)
    coord.signal_settings["ean_secondary"]["schedule"] = "Missing"
    coord.data = await coord._async_update_data()
    assert coord.data["ean_secondary"] is None
    assert sensors.HdoSensor(coord, "ean", "tariff", secondary=True).available is False
    assert sensors.HdoSensor(coord, "ean", "price").available is True


@pytest.mark.asyncio
async def test_legacy_single_signal_and_stale_data(modules):
    module, sensors = modules
    coord = setup(module, secondary=False)
    coord.data = await coord._async_update_data()
    assert set(coord.data) == {"ean"}
    coord.data["ean"]["fetched_at"] = datetime.now(UTC) - timedelta(hours=3)
    assert sensors.HdoSensor(coord, "ean", "tariff").available is False
