from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers.storage import Store

from .client import GarminLiveTrackClient
from .const import (
    CONF_DEFER_STARTUP_POLL_SECONDS,
    DEFAULT_DEFER_STARTUP_POLL_SECONDS,
    PLATFORMS,
    STORAGE_KEY,
    STORAGE_VERSION,
)
from .coordinator import GarminLiveTrackManager

_LOGGER = logging.getLogger(__name__)


@dataclass
class IntegrationRuntimeData:
    manager: GarminLiveTrackManager
    storage: Store
    unsub_options_update_listener: object | None = None
    unsub_recovery_fallback_1: object | None = None
    unsub_recovery_fallback_2: object | None = None
    recovery_attempts: int = 0
    recovery_complete: bool = False


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    session = aiohttp_client.async_get_clientsession(hass)
    storage = Store(hass, STORAGE_VERSION, STORAGE_KEY)
    manager = GarminLiveTrackManager(hass, GarminLiveTrackClient(hass, session), storage, {**entry.data, **entry.options})
    manager.startup_debug["setup_entry_started"] = datetime.now(UTC).isoformat()
    _LOGGER.warning("Garmin LiveTrack startup diag: setup_entry started")
    await manager.async_setup()
    manager.startup_debug["manager_setup_done"] = datetime.now(UTC).isoformat()
    _LOGGER.warning("Garmin LiveTrack startup diag: manager setup done")
    restored = await manager.async_restore_sessions_from_storage()
    manager.startup_debug["restore_sessions_done"] = datetime.now(UTC).isoformat()
    manager.startup_debug["restore_sessions_count"] = restored
    _LOGGER.warning("Garmin LiveTrack startup diag: restored %s session(s) from storage", restored)

    runtime = IntegrationRuntimeData(manager=manager, storage=storage)
    entry.runtime_data = runtime

    async def _options_updated(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        rt = config_entry.runtime_data
        rt.manager.options = {**config_entry.data, **config_entry.options}
        rt.manager._sync_client_options()
        rt.manager._apply_option_user_policies()
        await rt.manager._update_imap_listener()
        await rt.manager.async_save_storage()

    runtime.unsub_options_update_listener = entry.add_update_listener(_options_updated)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    manager.startup_debug["platform_forward_done"] = datetime.now(UTC).isoformat()
    _LOGGER.warning("Garmin LiveTrack startup diag: platform setup forwarded")
    def _schedule_task(coro) -> None:
        """Schedule a coroutine from any callback thread safely."""
        hass.loop.call_soon_threadsafe(hass.async_create_task, coro)

    async def _run_recovery_once() -> None:
        if runtime.recovery_complete:
            return
        manager.startup_debug["recovery_once_started"] = datetime.now(UTC).isoformat()
        runtime.recovery_attempts += 1
        delay = int(manager.options.get(CONF_DEFER_STARTUP_POLL_SECONDS, DEFAULT_DEFER_STARTUP_POLL_SECONDS))
        manager.startup_debug["defer_startup_poll_seconds"] = delay
        _LOGGER.warning(
            "Garmin LiveTrack startup diag: recovery attempt %s started, defer=%ss",
            runtime.recovery_attempts,
            delay,
        )
        if delay <= 0:
            started = await manager.async_start_restored_pollers()
            manager.startup_debug["restored_pollers_started"] = datetime.now(UTC).isoformat()
            manager.startup_debug["restored_pollers_started_count"] = started
            _LOGGER.warning("Garmin LiveTrack startup diag: started %s restored poller(s) immediately", started)
        else:
            async_call_later(
                hass,
                delay,
                lambda _now: _schedule_task(_start_restored_pollers_with_debug()),
            )
            manager.startup_debug["restored_pollers_scheduled_for"] = datetime.now(UTC).isoformat()
            _LOGGER.warning("Garmin LiveTrack startup diag: scheduled restored pollers in %ss", delay)
        # Keep startup lightweight: avoid long synchronous fetch passes here.
        # Restored pollers will fetch on their own loop.
        runtime.recovery_complete = True
        manager.startup_debug["recovery_once_completed"] = datetime.now(UTC).isoformat()
        _LOGGER.warning("Garmin LiveTrack startup diag: recovery scheduling completed")

    async def _start_restored_pollers_with_debug() -> None:
        manager.startup_debug["restored_pollers_start_task_fired"] = datetime.now(UTC).isoformat()
        started = await manager.async_start_restored_pollers()
        manager.startup_debug["restored_pollers_started_count"] = started
        manager.startup_debug["restored_pollers_started_at"] = datetime.now(UTC).isoformat()
        _LOGGER.warning("Garmin LiveTrack startup diag: delayed start launched %s restored poller(s)", started)

    if hass.is_running:
        hass.async_create_task(_run_recovery_once())
    else:
        hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STARTED,
            lambda _event: _schedule_task(_run_recovery_once()),
        )
    # Fallback safety nets in case startup-event timing is missed or storage is
    # briefly unavailable at the first attempt.
    runtime.unsub_recovery_fallback_1 = async_call_later(
        hass,
        45,
        lambda _now: _schedule_task(_run_recovery_once()),
    )
    runtime.unsub_recovery_fallback_2 = async_call_later(
        hass,
        120,
        lambda _now: _schedule_task(_run_recovery_once()),
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    runtime: IntegrationRuntimeData = entry.runtime_data
    if runtime.unsub_options_update_listener:
        runtime.unsub_options_update_listener()
    if runtime.unsub_recovery_fallback_1:
        runtime.unsub_recovery_fallback_1()
    if runtime.unsub_recovery_fallback_2:
        runtime.unsub_recovery_fallback_2()
    await runtime.manager.async_unload()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    return True
