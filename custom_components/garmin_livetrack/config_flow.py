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
    CONF_EXPOSE_DEBUG_ATTRIBUTES,
    CONF_FINALIZATION_MINUTES,
    CONF_INITIAL_TRACKPOINT_WAIT,
    CONF_LISTEN_TO_IMAP_EVENTS,
    CONF_MAX_RUNTIME_HOURS,
    CONF_RETAIN_ENDED_HOURS,
    CONF_STALE_MINUTES,
    CONF_STRICT_USERS,
    CONF_UPDATE_PROFILE,
    CONF_UPDATE_INTERVAL,
    CONF_USER_AGENT,
    CONF_USER_POLICIES,
    DEFAULT_ACCEPT_FIRST_SEEN_USERS,
    DEFAULT_ACTIVITY_FILTER,
    DEFAULT_ALLOWED_USERS,
    DEFAULT_DEFER_STARTUP_POLL_SECONDS,
    DEFAULT_EXPOSE_DEBUG_ATTRIBUTES,
    DEFAULT_FINALIZATION_MINUTES,
    DEFAULT_INITIAL_TRACKPOINT_WAIT,
    DEFAULT_LISTEN_TO_IMAP_EVENTS,
    DEFAULT_MAX_RUNTIME_HOURS,
    DEFAULT_RETAIN_ENDED_HOURS,
    DEFAULT_STALE_MINUTES,
    DEFAULT_STRICT_USERS,
    DEFAULT_UPDATE_PROFILE,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_USER_AGENT,
    DOMAIN,
    UPDATE_PROFILE_VALUES,
)

CONF_EDIT_USER = "edit_user"
CONF_USER_ACTIVITY_MODE = "user_activity_mode"

ERROR_INVALID_ALLOWED_ACTIVITIES = "invalid_allowed_activities"
ERROR_INVALID_USER_AGENT = "invalid_user_agent"


def _global_schema(
    defaults: dict,
    *,
    include_users: bool,
    known_users: list[str] | None = None,
) -> vol.Schema:
    fields = {
        vol.Required(
            CONF_LISTEN_TO_IMAP_EVENTS,
            default=defaults.get(CONF_LISTEN_TO_IMAP_EVENTS, DEFAULT_LISTEN_TO_IMAP_EVENTS),
        ): bool,
        vol.Optional(CONF_USER_AGENT): str,
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
            CONF_UPDATE_PROFILE,
            default=defaults.get(CONF_UPDATE_PROFILE, DEFAULT_UPDATE_PROFILE),
        ): vol.In(UPDATE_PROFILE_VALUES),
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
        vol.Required(
            CONF_EXPOSE_DEBUG_ATTRIBUTES,
            default=defaults.get(CONF_EXPOSE_DEBUG_ATTRIBUTES, DEFAULT_EXPOSE_DEBUG_ATTRIBUTES),
        ): bool,
    }
    if known_users:
        fields[vol.Optional(CONF_EDIT_USER)] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=known_users,
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        )
    return vol.Schema(fields)


def _user_policy_schema(defaults: dict) -> vol.Schema:
    allowed_activities = defaults.get("allowed_activities", []) or []
    activity_mode = "custom" if allowed_activities else "inherit_global"
    return vol.Schema(
        {
            vol.Required("enabled", default=defaults.get("enabled", True)): bool,
            vol.Required("mode", default=defaults.get("mode", "normal")): vol.In(
                ["normal", "register_only", "one_event_only"]
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
    user_agent = str(out.get(CONF_USER_AGENT, DEFAULT_USER_AGENT) or "").strip()
    if not user_agent:
        user_agent = DEFAULT_USER_AGENT
    if len(user_agent) > 256:
        raise vol.Invalid(ERROR_INVALID_USER_AGENT)
    out[CONF_USER_AGENT] = user_agent
    out.pop(CONF_EDIT_USER, None)
    return out


def _normalize_user_policy(inp: dict) -> dict:
    out = dict(inp)
    activities = out.get("allowed_activities")
    if isinstance(activities, list):
        out["allowed_activities"] = [str(part).strip().lower() for part in activities if str(part).strip()]
    else:
        raw_activities = str(activities or "")
        out["allowed_activities"] = [part.strip().lower() for part in raw_activities.split(",") if part.strip()]
    activity_mode = out.pop(CONF_USER_ACTIVITY_MODE, "inherit_global")
    if activity_mode == "inherit_global":
        out["allowed_activities"] = None
    elif not out["allowed_activities"]:
        raise vol.Invalid(ERROR_INVALID_ALLOWED_ACTIVITIES)
    return out


def _error_key(err: vol.Invalid) -> str:
    if str(err) == ERROR_INVALID_USER_AGENT:
        return ERROR_INVALID_USER_AGENT
    return ERROR_INVALID_ALLOWED_ACTIVITIES


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
            data_schema=self.add_suggested_values_to_schema(
                _global_schema({}, include_users=False),
                {CONF_USER_AGENT: DEFAULT_USER_AGENT},
            ),
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
            data_schema=self.add_suggested_values_to_schema(
                _global_schema(
                    defaults,
                    include_users=True,
                    known_users=known_users,
                ),
                {
                    CONF_USER_AGENT: defaults.get(CONF_USER_AGENT, DEFAULT_USER_AGENT),
                },
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
            data_schema=_user_policy_schema(existing),
            errors=errors,
            description_placeholders={"user": selected_user},
        )
