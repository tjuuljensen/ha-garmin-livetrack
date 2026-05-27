from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers.storage import Store

from .client import GarminLiveTrackClient
from .const import PLATFORMS, STORAGE_KEY, STORAGE_VERSION
from .coordinator import GarminLiveTrackManager


@dataclass
class IntegrationRuntimeData:
    manager: GarminLiveTrackManager
    storage: Store
    unsub_options_update_listener: object | None = None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    session = aiohttp_client.async_get_clientsession(hass)
    storage = Store(hass, STORAGE_VERSION, STORAGE_KEY)
    manager = GarminLiveTrackManager(hass, GarminLiveTrackClient(hass, session), storage, {**entry.data, **entry.options})
    await manager.async_setup()

    runtime = IntegrationRuntimeData(manager=manager, storage=storage)
    entry.runtime_data = runtime

    async def _options_updated(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        rt = config_entry.runtime_data
        rt.manager.options = {**config_entry.data, **config_entry.options}
        await rt.manager._update_imap_listener()
        await rt.manager.async_save_storage()

    runtime.unsub_options_update_listener = entry.add_update_listener(_options_updated)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await manager.async_recover_sessions()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    runtime: IntegrationRuntimeData = entry.runtime_data
    if runtime.unsub_options_update_listener:
        runtime.unsub_options_update_listener()
    await runtime.manager.async_unload()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    return True