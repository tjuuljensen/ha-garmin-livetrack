from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries

from .const import *


def _schema(defaults: dict) -> vol.Schema:
    return vol.Schema({
        vol.Required(CONF_LISTEN_TO_IMAP_EVENTS, default=defaults.get(CONF_LISTEN_TO_IMAP_EVENTS, DEFAULT_LISTEN_TO_IMAP_EVENTS)): bool,
        vol.Required(CONF_ENABLE_NOTIFICATIONS, default=defaults.get(CONF_ENABLE_NOTIFICATIONS, DEFAULT_ENABLE_NOTIFICATIONS)): bool,
        vol.Required(CONF_NOTIFY_SERVICE, default=defaults.get(CONF_NOTIFY_SERVICE, DEFAULT_NOTIFY_SERVICE)): str,
        vol.Required(CONF_IOS_NOTIFICATION_STYLE, default=defaults.get(CONF_IOS_NOTIFICATION_STYLE, DEFAULT_IOS_NOTIFICATION_STYLE)): bool,
        vol.Required(CONF_STRICT_USERS, default=defaults.get(CONF_STRICT_USERS, DEFAULT_STRICT_USERS)): bool,
        vol.Required(CONF_ACCEPT_FIRST_SEEN_USERS, default=defaults.get(CONF_ACCEPT_FIRST_SEEN_USERS, DEFAULT_ACCEPT_FIRST_SEEN_USERS)): bool,
        vol.Required(CONF_ALLOWED_USERS, default=", ".join(defaults.get(CONF_ALLOWED_USERS, DEFAULT_ALLOWED_USERS))): str,
        vol.Required(CONF_ACTIVITY_FILTER, default=defaults.get(CONF_ACTIVITY_FILTER, DEFAULT_ACTIVITY_FILTER)): vol.In(ACTIVITY_VALUES),
        vol.Required(CONF_UPDATE_INTERVAL, default=defaults.get(CONF_UPDATE_INTERVAL, int(DEFAULT_UPDATE_INTERVAL.total_seconds()))): vol.All(int, vol.Range(min=15)),
        vol.Required(CONF_INITIAL_TRACKPOINT_WAIT, default=defaults.get(CONF_INITIAL_TRACKPOINT_WAIT, int(DEFAULT_INITIAL_TRACKPOINT_WAIT.total_seconds() / 60))): vol.All(int, vol.Range(min=1)),
        vol.Required(CONF_MAX_RUNTIME_HOURS, default=defaults.get(CONF_MAX_RUNTIME_HOURS, DEFAULT_MAX_RUNTIME_HOURS)): vol.All(int, vol.Range(min=1, max=48)),
        vol.Required(CONF_STALE_MINUTES, default=defaults.get(CONF_STALE_MINUTES, DEFAULT_STALE_MINUTES)): vol.All(int, vol.Range(min=2)),
        vol.Required(CONF_FINALIZATION_MINUTES, default=defaults.get(CONF_FINALIZATION_MINUTES, DEFAULT_FINALIZATION_MINUTES)): vol.All(int, vol.Range(min=0)),
        vol.Required(CONF_RETAIN_ENDED_HOURS, default=defaults.get(CONF_RETAIN_ENDED_HOURS, DEFAULT_RETAIN_ENDED_HOURS)): vol.All(int, vol.Range(min=1)),
    })


def _normalize(inp: dict) -> dict:
    out = dict(inp)
    if out.get(CONF_NOTIFY_SERVICE) and not str(out[CONF_NOTIFY_SERVICE]).startswith("notify."):
        raise vol.Invalid("notify_service must look like notify.<target>")
    out[CONF_ALLOWED_USERS] = [u.strip() for u in str(out.get(CONF_ALLOWED_USERS, "")).split(",") if u.strip()]
    return out


class GarminLiveTrackConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            return self.async_create_entry(title="Garmin LiveTrack", data=_normalize(user_input))
        return self.async_show_form(step_id="user", data_schema=_schema({}))

    @staticmethod
    def async_get_options_flow(config_entry):
        return GarminLiveTrackOptionsFlow(config_entry)


class GarminLiveTrackOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry):
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        defaults = {**self.config_entry.data, **self.config_entry.options}
        if user_input is not None:
            return self.async_create_entry(title="", data=_normalize(user_input))
        return self.async_show_form(step_id="init", data_schema=_schema(defaults))