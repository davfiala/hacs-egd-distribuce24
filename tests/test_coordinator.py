"""Coordinator behavior with lightweight HA storage/recorder contract doubles.

These tests do not replace installation testing in a real Home Assistant instance.
"""

import importlib
import sys
import types
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from egd_client_test.api import AuthenticationError, Reading

EAN = "859182400000000000"
OTHER = "859182400000000001"


@pytest.fixture
def environment(monkeypatch):
    published = []

    class Store:
        def __init__(self, *args):
            self.async_load = AsyncMock(return_value=None)
            self.async_save = AsyncMock()

    class Coordinator:
        def __init__(self, hass, logger, **kwargs):
            self.hass = hass

    class AuthFailed(Exception):
        pass

    class UpdateFailed(Exception):
        pass

    modules = {
        "homeassistant.components.recorder.models": {
            "StatisticMeanType": types.SimpleNamespace(NONE=0)
        },
        "homeassistant.components.recorder.statistics": {
            "async_add_external_statistics": lambda *args: published.append(args)
        },
        "homeassistant.exceptions": {"ConfigEntryAuthFailed": AuthFailed},
        "homeassistant.helpers.storage": {"Store": Store},
        "homeassistant.helpers.update_coordinator": {
            "DataUpdateCoordinator": Coordinator,
            "UpdateFailed": UpdateFailed,
        },
    }
    for name, attrs in modules.items():
        module = types.ModuleType(name)
        module.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, module)
    sys.modules.pop("egd_client_test.coordinator", None)
    module = importlib.import_module("egd_client_test.coordinator")

    class FixedTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 21, 14, 37, tzinfo=UTC).astimezone(tz)

    monkeypatch.setattr(module, "datetime", FixedTime)
    entry = types.SimpleNamespace(entry_id="test", data={"meters": [EAN], "include_export": False})
    start = datetime(2026, 9, 20, tzinfo=UTC)
    rows = [Reading(start + timedelta(minutes=i * 15), Decimal("0.1"), "W") for i in range(4)]
    client = types.SimpleNamespace(
        meters=AsyncMock(
            return_value=[{"ean": EAN, "typMereni": "C1"}, {"ean": OTHER, "typMereni": "C1"}]
        ),
        readings=AsyncMock(return_value=rows),
    )
    coordinator = module.EgdCoordinator(object(), entry, client)
    yield coordinator, client, published, module
    sys.modules.pop("egd_client_test.coordinator", None)


@pytest.mark.asyncio
async def test_only_selected_meter_downloaded(environment):
    coordinator, client, published, _ = environment
    data = await coordinator._async_update_data()
    assert client.readings.await_count == 1
    assert client.readings.await_args.args[:2] == (EAN, "DCQC")
    assert list(data["streams"]) == [EAN + "_dcqc"]
    assert published[0][1]["unit_class"] == "energy"
    assert published[0][2][-1]["sum"] == 0.4


@pytest.mark.asyncio
async def test_export_opt_in(environment):
    coordinator, client, _, _ = environment
    coordinator.entry.data["include_export"] = True
    await coordinator._async_update_data()
    assert [c.args[1] for c in client.readings.await_args_list] == ["DCQC", "DSQC"]


@pytest.mark.asyncio
async def test_same_day_no_network_poll(environment):
    coordinator, client, _, _ = environment
    await coordinator._async_update_data()
    await coordinator._async_update_data()
    assert client.meters.await_count == 1


@pytest.mark.asyncio
async def test_startup_replays_saved_archive(environment):
    coordinator, _, published, _ = environment
    data = await coordinator._async_update_data()
    coordinator.store.async_load.return_value = data
    await coordinator._async_setup()
    assert published[0] == published[1]


@pytest.mark.asyncio
async def test_storage_failure_does_not_publish(environment):
    coordinator, _, published, _ = environment
    coordinator.store.async_save.side_effect = OSError("disk full")
    with pytest.raises(OSError):
        await coordinator._async_update_data()
    assert not published
    assert coordinator.archive["last_sync"] is None


@pytest.mark.asyncio
async def test_auth_failure_requests_reauth(environment):
    coordinator, client, _, module = environment
    client.meters.side_effect = AuthenticationError("rejected")
    with pytest.raises(module.ConfigEntryAuthFailed):
        await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_removed_meter_not_silently_ignored(environment):
    coordinator, client, _, module = environment
    client.meters.return_value = []
    with pytest.raises(module.UpdateFailed):
        await coordinator._async_update_data()
