"""Hourly HDO download independent of the delayed measurement service."""

import logging
from datetime import UTC, datetime, timedelta

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .hdo import HdoClient, HdoError, select_records, tariff_state


class HdoCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry, session):
        super().__init__(
            hass,
            logging.getLogger(__name__),
            config_entry=entry,
            name="EG.D HDO",
            update_interval=timedelta(hours=1),
        )
        self.settings = entry.data.get("hdo_settings", {})
        self.client = HdoClient(session)

    async def _async_update_data(self):
        try:
            regions, records = await self.client.catalog()
        except HdoError as err:
            raise UpdateFailed("EG.D HDO schedule could not be downloaded") from err
        now = datetime.now(UTC)
        result = {}
        for ean, settings in self.settings.items():
            try:
                selected = select_records(regions, records, settings)
                tariff_state(selected, now)
                result[ean] = {"records": selected, "fetched_at": now}
            except HdoError:
                # Failure of one meter must not change another meter to VT.
                result[ean] = None
        return result
