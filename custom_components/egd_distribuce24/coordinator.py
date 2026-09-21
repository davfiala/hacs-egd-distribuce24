"""Daily downloads and replayable historical statistics."""

import copy
import logging
from datetime import UTC, datetime, time, timedelta

from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import PRAGUE, ApiError, AuthenticationError
from .const import CONF_EXPORT, DOMAIN, PROFILES
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

    async def _async_setup(self):
        saved = await self.store.async_load()
        if saved:
            self.archive = saved
            self._publish(saved)

    def _publish(self, archive):
        for key, stream in archive["streams"].items():
            rows = statistics(stream["hours"])
            if not rows:
                continue
            async_add_external_statistics(
                self.hass,
                {
                    "statistic_id": f"{DOMAIN}:{key}",
                    "source": DOMAIN,
                    "name": f"EG.D {stream['ean']} {stream['profile']}",
                    "unit_of_measurement": "kWh",
                    "unit_class": "energy",
                    "has_sum": True,
                    "mean_type": StatisticMeanType.NONE,
                },
                rows,
            )

    async def _async_update_data(self):
        now = datetime.now(PRAGUE)
        previous = self.archive.get("last_sync")
        if previous:
            last = datetime.fromisoformat(previous).astimezone(PRAGUE)
            if (last.date() == now.date() and last.time() >= time(12, 17)) or now.time() < time(
                12, 17
            ):
                return self.archive
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
                    readings = await self.client.readings(meter["ean"], profile, start, end)
                    stream["hours"].update(complete_hours(readings))
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
