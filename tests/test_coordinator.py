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
        current = datetime(2026, 9, 21, 14, 37, tzinfo=UTC)

        @classmethod
        def now(cls, tz=None):
            return cls.current.astimezone(tz)

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
async def test_friendly_names_preserve_statistic_ids(environment):
    coordinator, _, published, _ = environment
    coordinator.entry.data.update(
        {
            "include_export": True,
            "import_name": " Spotřeba domu ",
            "export_name": "Prodej FVE",
        }
    )
    await coordinator._async_update_data()
    assert published[0][1]["name"] == f"EG.D {EAN} Spotřeba domu"
    assert published[0][1]["statistic_id"] == f"egd_distribuce24:{EAN}_dcqc"
    assert published[1][1]["name"] == f"EG.D {EAN} Prodej FVE"
    assert published[1][1]["statistic_id"] == f"egd_distribuce24:{EAN}_dsqc"


@pytest.mark.asyncio
@pytest.mark.parametrize("hour", [5, 14])
async def test_next_hour_downloads_again_before_and_after_noon(environment, hour):
    coordinator, client, published, module = environment
    module.datetime.current = datetime(2026, 9, 21, hour, 37, tzinfo=UTC)
    await coordinator._async_update_data()
    first_sync = coordinator.archive["last_sync"]
    module.datetime.current += timedelta(hours=1)
    await coordinator._async_update_data()
    assert client.meters.await_count == 2
    assert client.readings.await_count == 2
    assert coordinator.archive["last_sync"] != first_sync
    assert published[0] == published[1]


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


@pytest.mark.asyncio
async def test_cost_backfill_persists_quarters_and_does_not_charge_export(environment):
    from egd_client_test.costs import validate_cost_settings

    coordinator, client, published, module = environment
    coordinator.entry.data.update(
        {
            "include_export": True,
            "cost_settings": {
                EAN: validate_cost_settings(
                    {"monthly_fee": "300", "backfill_prices": True, "backfill_from": "2026-08-01"},
                    module.datetime.current,
                )
            },
            "hdo_settings": {EAN: {"price_nt": 5, "price_vt": 5}},
        }
    )
    data = await coordinator._async_update_data()
    assert client.readings.await_args_list[0].args[2] == datetime(2026, 7, 31, 22, tzinfo=UTC)
    assert len(data["streams"][EAN + "_dcqc"]["quarters"]) == 4
    assert "cost_ledger" not in data["streams"][EAN + "_dsqc"]
    money = [item for item in published if item[1]["unit_of_measurement"] == "CZK"]
    assert len(money) == 3
    assert money[0][2][-1]["sum"] == 2
    published.clear()
    await coordinator._async_update_data()
    assert client.readings.await_args_list[2].args[2] > datetime(2026, 8, 1, tzinfo=UTC)
    assert [item for item in published if item[1]["unit_of_measurement"] == "CZK"] == money
