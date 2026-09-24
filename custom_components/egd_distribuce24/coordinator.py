"""Hourly downloads and replayable historical statistics."""

import copy
import logging
from datetime import UTC, datetime, time, timedelta

from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import PRAGUE, ApiError, AuthenticationError
from .const import CONF_EXPORT, DOMAIN, PROFILES, profile_name
from .costs import capture_schedules, cost_statistics, update_journal
from .energy import complete_hours, statistics

_LOGGER = logging.getLogger(__name__)


class EgdCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry, client):
        super().__init__(
            hass, _LOGGER, name=DOMAIN, config_entry=entry, update_interval=timedelta(hours=1)
        )
        self.client = client
        self.entry = entry
        self.store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}")
        self.archive = {"streams": {}, "last_sync": None}
        self.hdo = None

    async def _async_setup(self):
        saved = await self.store.async_load()
        if saved:
            self.archive = saved
            self._publish(saved)

    def _publish(self, archive):
        for key, stream in archive["streams"].items():
            if stream.get("cost_ledger") and archive.get("last_sync"):
                end = datetime.combine(
                    datetime.fromisoformat(archive["last_sync"]).astimezone(PRAGUE).date(),
                    time.min,
                    PRAGUE,
                ).astimezone(UTC)
                costs = cost_statistics(stream.get("quarters", {}), stream["cost_ledger"], end)
                stream["priced_hours"] = costs["priced_hours"]
                for kind, label in (
                    ("energy_cost", "Cena spotřeby"),
                    ("standing_cost", "Stálé platby"),
                    ("total_cost", "Celkové náklady"),
                ):
                    if costs[kind]:
                        async_add_external_statistics(
                            self.hass,
                            {
                                "statistic_id": f"{DOMAIN}:{key}_{kind}",
                                "source": DOMAIN,
                                "name": f"EG.D {stream['ean']} {label}",
                                "unit_of_measurement": "CZK",
                                "unit_class": None,
                                "has_sum": True,
                                "mean_type": StatisticMeanType.NONE,
                            },
                            costs[kind],
                        )
            rows = statistics(stream["hours"])
            if not rows:
                continue
            async_add_external_statistics(
                self.hass,
                {
                    "statistic_id": f"{DOMAIN}:{key}",
                    "source": DOMAIN,
                    "name": f"EG.D {stream['ean']} {profile_name(self.entry.data, stream['profile'])}",
                    "unit_of_measurement": "kWh",
                    "unit_class": "energy",
                    "has_sum": True,
                    "mean_type": StatisticMeanType.NONE,
                },
                rows,
            )

    async def _async_update_data(self):
        now = datetime.now(PRAGUE)
        # Yesterday ends at midnight in Prague, not midnight UTC.
        end = datetime.combine(now.date(), time.min, PRAGUE).astimezone(UTC)
        draft = copy.deepcopy(self.archive)
        try:
            meters = await self.client.meters()
            selected = set(self.entry.data["meters"])
            if not selected <= {m["ean"] for m in meters if m["typMereni"] in PROFILES}:
                raise ApiError("Selected meter is no longer accessible or supported")
            for meter in meters:
                if meter["ean"] not in selected or meter["typMereni"] not in PROFILES:
                    continue
                profiles = PROFILES[meter["typMereni"]]
                if not self.entry.data.get(CONF_EXPORT, False):
                    profiles = profiles[:1]
                for profile in profiles:
                    key = f"{meter['ean']}_{profile.lower()}"
                    stream = draft["streams"].setdefault(
                        key,
                        {
                            "ean": meter["ean"],
                            "profile": profile,
                            "hours": {},
                            "latest": None,
                            "queried_until": None,
                        },
                    )
                    start = end - timedelta(days=30)
                    if stream["queried_until"]:
                        start = datetime.fromisoformat(stream["queried_until"]) - timedelta(days=14)
                    config = self.entry.data.get("cost_settings", {}).get(meter["ean"])
                    if profile != PROFILES[meter["typMereni"]][0]:
                        config = None  # A fixed fee belongs to the meter's import only.
                    if config:
                        hdo_settings = self.entry.data.get("hdo_settings", {}).get(meter["ean"])
                        hdo_data = (self.hdo.data or {}).get(meter["ean"]) if self.hdo else None
                        update_journal(
                            stream.setdefault("cost_ledger", {}),
                            config,
                            hdo_settings,
                            (hdo_data or {}).get("records"),
                        )
                        if (
                            config["backfill_prices"]
                            and stream.get("cost_backfill_revision") != config["effective_from"]
                        ):
                            start = min(
                                start,
                                datetime.combine(
                                    datetime.fromisoformat(config["backfill_from"]).date(),
                                    time.min,
                                    PRAGUE,
                                ).astimezone(UTC),
                            )
                    readings = await self.client.readings(meter["ean"], profile, start, end)
                    hours = complete_hours(readings)
                    stream["hours"].update(hours)
                    if config:
                        quarters = stream.setdefault("quarters", {})
                        for reading in readings:
                            hour = reading.timestamp.replace(minute=0, second=0, microsecond=0)
                            if hour.isoformat() in hours:
                                quarters[reading.timestamp.isoformat()] = {
                                    "value": str(reading.value),
                                    "status": reading.status,
                                }
                        stream["cost_backfill_revision"] = config["effective_from"]
                        capture_schedules(
                            stream["cost_ledger"],
                            config["effective_from"],
                            quarters,
                            (hdo_data or {}).get("records"),
                        )
                    stream["queried_until"] = end.isoformat()
                    if readings:
                        newest = readings[-1]
                        stream["latest"] = {
                            "timestamp": newest.timestamp.isoformat(),
                            "value": float(newest.value) if newest.value is not None else None,
                            "status": newest.status,
                        }
            draft["last_sync"] = now.isoformat()
            # Save before enqueueing recorder writes. Startup replays the journal
            # if HA stops between these operations; absolute sums prevent doubling.
            await self.store.async_save(draft)
            self._publish(draft)
            self.archive = draft
            return draft
        except AuthenticationError as err:
            raise ConfigEntryAuthFailed("EG.D credentials rejected") from err
        except ApiError as err:
            raise UpdateFailed(str(err)) from err
