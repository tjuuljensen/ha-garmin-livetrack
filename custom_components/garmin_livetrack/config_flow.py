from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector

from .const import (
    ACTIVITY_VALUES,
    CONF_ACCEPT_FIRST_SEEN_USERS,
    CONF_ACTIVITY_FILTER,
    CONF_ALLOWED_USERS,
    CONF_DEFER_STARTUP_POLL_SECONDS,
    CONF_ENABLE_NOTIFICATIONS,
    CONF_FINALIZATION_MINUTES,
    CONF_INITIAL_TRACKPOINT_WAIT,
    CONF_IOS_NOTIFICATION_STYLE,
    CONF_LISTEN_TO_IMAP_EVENTS,
    CONF_MAX_RUNTIME_HOURS,
    CONF_NOTIFY_SERVICE,
    CONF_RETAIN_ENDED_HOURS,
    CONF_STALE_MINUTES,
    CONF_STRICT_USERS,
    CONF_UPDATE_INTERVAL,
    CONF_USER_POLICIES,
    DEFAULT_ACCEPT_FIRST_SEEN_USERS,
    DEFAULT_ACTIVITY_FILTER,
    DEFAULT_ALLOWED_USERS,
    DEFAULT_DEFER_STARTUP_POLL_SECONDS,
    DEFAULT_ENABLE_NOTIFICATIONS,
    DEFAULT_FINALIZATION_MINUTES,
    DEFAULT_INITIAL_TRACKPOINT_WAIT,
    DEFAULT_IOS_NOTIFICATION_STYLE,
    DEFAULT_LISTEN_TO_IMAP_EVENTS,
    DEFAULT_MAX_RUNTIME_HOURS,
    DEFAULT_NOTIFY_SERVICE,
    DEFAULT_RETAIN_ENDED_HOURS,
    DEFAULT_STALE_MINUTES,
    DEFAULT_STRICT_USERS,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
)

CONF_EDIT_USER = "edit_user"
CONF_USER_ENABLE_NOTIFICATIONS_MODE = "user_enable_notifications_mode"
CONF_USER_IOS_NOTIFICATION_STYLE_MODE = "user_ios_notification_style_mode"
CONF_USER_ACTIVITY_MODE = "user_activity_mode"

ERROR_INVALID_NOTIFY_SERVICE = "invalid_notify_service"
ERROR_INVALID_ALLOWED_ACTIVITIES = "invalid_allowed_activities"


def _notify_service_options(services: list[str], current: str | None = None, *, include_inherit: bool = False) -> list[str]:
    options: list[str] = ["inherit_global"] if include_inherit else []
    options.extend(services)
    current_value = (current or "").strip()
    if current_value and current_value not in options:
        options.append(current_value)
    return sorted(set(options), key=str.lower)


def _global_schema(
    defaults: dict,
    *,
    include_users: bool,
    known_users: list[str] | None = None,
    notify_services: list[str] | None = None,
) -> vol.Schema:
    notify_choices = _notify_service_options(
        notify_services or [DEFAULT_NOTIFY_SERVICE],
        defaults.get(CONF_NOTIFY_SERVICE),
    )
    fields = {
            vol.Required(
                CONF_LISTEN_TO_IMAP_EVENTS,
                default=defaults.get(CONF_LISTEN_TO_IMAP_EVENTS, DEFAULT_LISTEN_TO_IMAP_EVENTS),
            ): bool,
            vol.Required(
                CONF_ENABLE_NOTIFICATIONS,
                default=defaults.get(CONF_ENABLE_NOTIFICATIONS, DEFAULT_ENABLE_NOTIFICATIONS),
            ): bool,
            vol.Required(
                CONF_NOTIFY_SERVICE,
                default=defaults.get(CONF_NOTIFY_SERVICE, DEFAULT_NOTIFY_SERVICE),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=notify_choices,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(
                CONF_IOS_NOTIFICATION_STYLE,
                default=defaults.get(CONF_IOS_NOTIFICATION_STYLE, DEFAULT_IOS_NOTIFICATION_STYLE),
            ): bool,
            vol.Required(
                CONF_STRICT_USERS,
                default=defaults.get(CONF_STRICT_USERS, DEFAULT_STRICT_USERS),
            ): bool,
            vol.Required(
                CONF_ACCEPT_FIRST_SEEN_USERS,
                default=defaults.get(CONF_ACCEPT_FIRST_SEEN_USERS, DEFAULT_ACCEPT_FIRST_SEEN_USERS),
            ): bool,
            vol.Required(
                CONF_ALLOWED_USERS,
                default=", ".join(defaults.get(CONF_ALLOWED_USERS, DEFAULT_ALLOWED_USERS)) if include_users else "",
            ): str,
            vol.Required(
                CONF_ACTIVITY_FILTER,
                default=defaults.get(CONF_ACTIVITY_FILTER, DEFAULT_ACTIVITY_FILTER),
            ): vol.In(ACTIVITY_VALUES),
            vol.Required(
                CONF_UPDATE_INTERVAL,
                default=defaults.get(CONF_UPDATE_INTERVAL, int(DEFAULT_UPDATE_INTERVAL.total_seconds())),
            ): vol.All(int, vol.Range(min=30)),
            vol.Required(
                CONF_INITIAL_TRACKPOINT_WAIT,
                default=defaults.get(
                    CONF_INITIAL_TRACKPOINT_WAIT,
                    int(DEFAULT_INITIAL_TRACKPOINT_WAIT.total_seconds() / 60),
                ),
            ): vol.All(int, vol.Range(min=1)),
            vol.Required(
                CONF_MAX_RUNTIME_HOURS,
                default=defaults.get(CONF_MAX_RUNTIME_HOURS, DEFAULT_MAX_RUNTIME_HOURS),
            ): vol.All(int, vol.Range(min=1, max=48)),
            vol.Required(
                CONF_STALE_MINUTES,
                default=defaults.get(CONF_STALE_MINUTES, DEFAULT_STALE_MINUTES),
            ): vol.All(int, vol.Range(min=2)),
            vol.Required(
                CONF_FINALIZATION_MINUTES,
                default=defaults.get(CONF_FINALIZATION_MINUTES, DEFAULT_FINALIZATION_MINUTES),
            ): vol.All(int, vol.Range(min=0)),
            vol.Required(
                CONF_RETAIN_ENDED_HOURS,
                default=defaults.get(CONF_RETAIN_ENDED_HOURS, DEFAULT_RETAIN_ENDED_HOURS),
            ): vol.All(int, vol.Range(min=1)),
            vol.Required(
                CONF_DEFER_STARTUP_POLL_SECONDS,
                default=defaults.get(
                    CONF_DEFER_STARTUP_POLL_SECONDS,
                    DEFAULT_DEFER_STARTUP_POLL_SECONDS,
                ),
            ): vol.All(int, vol.Range(min=0, max=900)),
        }
    if known_users:
        fields[vol.Optional(CONF_EDIT_USER)] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=known_users,
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        )
    return vol.Schema(fields)


def _user_policy_schema(defaults: dict, notify_services: list[str] | None = None) -> vol.Schema:
    enable_notifications = defaults.get("enable_notifications")
    ios_notification_style = defaults.get("ios_notification_style")
    allowed_activities = defaults.get("allowed_activities", []) or []
    enable_mode = "inherit"
    ios_mode = "inherit"
    activity_mode = "custom" if allowed_activities else "inherit_global"
    notify_choices = _notify_service_options(
        notify_services or [DEFAULT_NOTIFY_SERVICE],
        defaults.get("notify_service"),
        include_inherit=True,
    )
    if enable_notifications is True:
        enable_mode = "enabled"
    elif enable_notifications is False:
        enable_mode = "disabled"
    if ios_notification_style is True:
        ios_mode = "enabled"
    elif ios_notification_style is False:
        ios_mode = "disabled"
    return vol.Schema(
        {
            vol.Required("enabled", default=defaults.get("enabled", True)): bool,
            vol.Required("mode", default=defaults.get("mode", "normal")): vol.In(
                ["normal", "register_only", "one_event_only"]
            ),
            vol.Required(CONF_USER_ENABLE_NOTIFICATIONS_MODE, default=enable_mode): vol.In(
                ["inherit", "enabled", "disabled"]
            ),
            vol.Required(
                "notify_service",
                default=defaults.get("notify_service") or "inherit_global",
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=notify_choices,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(CONF_USER_IOS_NOTIFICATION_STYLE_MODE, default=ios_mode): vol.In(
                ["inherit", "enabled", "disabled"]
            ),
            vol.Required(
                CONF_USER_ACTIVITY_MODE,
                default=activity_mode,
            ): vol.In(["inherit_global", "custom"]),
            vol.Optional(
                "allowed_activities",
                default=allowed_activities,
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[item for item in ACTIVITY_VALUES if item != "all"],
                    multiple=True,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
        }
    )


def _normalize(inp: dict, *, include_users: bool) -> dict:
    out = dict(inp)
    raw_users = str(out.get(CONF_ALLOWED_USERS, "") or "")
    out[CONF_ALLOWED_USERS] = [u.strip() for u in raw_users.split(",") if u.strip()] if include_users else []
    notify_service = str(out.get(CONF_NOTIFY_SERVICE, "") or "").strip()
    if notify_service and not notify_service.startswith("notify."):
        raise vol.Invalid("notify_service must look like notify.<target>")
    out[CONF_NOTIFY_SERVICE] = notify_service
    out.pop(CONF_EDIT_USER, None)
    return out


def _normalize_user_policy(inp: dict) -> dict:
    out = dict(inp)
    notify_service = str(out.get("notify_service", "") or "").strip()
    if notify_service == "inherit_global":
        notify_service = ""
    if notify_service and not notify_service.startswith("notify."):
        raise vol.Invalid("notify_service must look like notify.<target>")
    out["notify_service"] = notify_service or None
    activities = out.get("allowed_activities")
    if isinstance(activities, list):
        out["allowed_activities"] = [str(part).strip().lower() for part in activities if str(part).strip()]
    else:
        raw_activities = str(activities or "")
        out["allowed_activities"] = [part.strip().lower() for part in raw_activities.split(",") if part.strip()]
    enable_mode = out.pop(CONF_USER_ENABLE_NOTIFICATIONS_MODE, "inherit")
    ios_mode = out.pop(CONF_USER_IOS_NOTIFICATION_STYLE_MODE, "inherit")
    activity_mode = out.pop(CONF_USER_ACTIVITY_MODE, "inherit_global")
    out["enable_notifications"] = None if enable_mode == "inherit" else enable_mode == "enabled"
    out["ios_notification_style"] = None if ios_mode == "inherit" else ios_mode == "enabled"
    if activity_mode == "inherit_global":
        out["allowed_activities"] = None
    elif not out["allowed_activities"]:
        raise vol.Invalid(ERROR_INVALID_ALLOWED_ACTIVITIES)
    return out


def _error_key(err: vol.Invalid) -> str:
    if str(err) == ERROR_INVALID_ALLOWED_ACTIVITIES:
        return ERROR_INVALID_ALLOWED_ACTIVITIES
    return ERROR_INVALID_NOTIFY_SERVICE


class GarminLiveTrackConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        errors = {}
        if user_input is not None:
            try:
                return self.async_create_entry(
                    title="Garmin LiveTrack",
                    data=_normalize(user_input, include_users=False),
                )
            except vol.Invalid as err:
                errors["base"] = _error_key(err)
        return self.async_show_form(
            step_id="user",
            data_schema=_global_schema({}, include_users=False),
            errors=errors,
        )

    @staticmethod
    def async_get_options_flow(config_entry):
        return GarminLiveTrackOptionsFlow(config_entry)


class GarminLiveTrackOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry):
        self._config_entry = config_entry
        self._pending_options: dict | None = None
        self._selected_user: str | None = None

    def _known_users(self) -> list[str]:
        defaults = {**self._config_entry.data, **self._config_entry.options}
        users = defaults.get(CONF_ALLOWED_USERS, []) or []
        policies = defaults.get(CONF_USER_POLICIES, {}) or {}
        user_names = {u for u in users if isinstance(u, str) and u.strip()}
        for row in policies.values():
            if isinstance(row, dict):
                name = str(row.get("name", "") or "").strip()
                if name:
                    user_names.add(name)
        user_names.update(str(name).strip() for name in policies.keys() if str(name).strip())
        return sorted(user_names, key=str.lower)

    def _notify_services(self) -> list[str]:
        services = self.hass.services.async_services().get("notify", {})
        names = [f"notify.{service_name}" for service_name in services]
        if DEFAULT_NOTIFY_SERVICE not in names:
            names.append(DEFAULT_NOTIFY_SERVICE)
        return sorted(set(names), key=str.lower)

    async def async_step_init(self, user_input=None):
        defaults = {**self._config_entry.data, **self._config_entry.options}
        known_users = self._known_users()
        errors = {}
        if user_input is not None:
            try:
                normalized = _normalize(user_input, include_users=True)
                selected_user = str(user_input.get(CONF_EDIT_USER, "") or "").strip()
                if selected_user:
                    self._pending_options = {**defaults, **normalized}
                    self._selected_user = selected_user
                    return await self.async_step_user_policy()
                return self.async_create_entry(title="", data={**defaults, **normalized})
            except vol.Invalid as err:
                errors["base"] = _error_key(err)
        return self.async_show_form(
            step_id="init",
            data_schema=_global_schema(
                defaults,
                include_users=True,
                known_users=known_users,
                notify_services=self._notify_services(),
            ),
            errors=errors,
        )

    async def async_step_user_policy(self, user_input=None):
        defaults = self._pending_options or {**self._config_entry.data, **self._config_entry.options}
        user_policies = dict(defaults.get(CONF_USER_POLICIES, {}) or {})
        selected_user = self._selected_user or ""
        if not selected_user:
            return await self.async_step_init()
        existing = dict(user_policies.get(selected_user, {}) or {})
        if not existing:
            for key, row in user_policies.items():
                if str(key).strip().lower() == selected_user.lower() and isinstance(row, dict):
                    existing = dict(row)
                    break
        errors = {}
        if user_input is not None:
            try:
                normalized_policy = _normalize_user_policy(user_input)
                normalized_policy["name"] = selected_user
                user_policies[selected_user] = normalized_policy
                merged = {**defaults, CONF_USER_POLICIES: user_policies}
                return self.async_create_entry(title="", data=merged)
            except vol.Invalid as err:
                errors["base"] = _error_key(err)
        return self.async_show_form(
            step_id="user_policy",
            data_schema=_user_policy_schema(existing, notify_services=self._notify_services()),
            errors=errors,
            description_placeholders={"user": selected_user},
        )
