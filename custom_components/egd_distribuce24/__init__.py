"""EG.D Distribuce24 integration."""

from homeassistant.const import Platform
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EgdClient
from .const import CONF_CLIENT_ID, CONF_CLIENT_SECRET
from .coordinator import EgdCoordinator

PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass, entry):
    client = EgdClient(
        async_get_clientsession(hass), entry.data[CONF_CLIENT_ID], entry.data[CONF_CLIENT_SECRET]
    )
    coordinator = EgdCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass, entry):
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
