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
    CONF_USE_GARMIN_TRACKPOINT_FREQUENCY,
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
    DEFAULT_USE_GARMIN_TRACKPOINT_FREQUENCY,
    DEFAULT_USER_AGENT,
    DOMAIN,
    UPDATE_PROFILE_VALUES,
)

CONF_EDIT_USER = "edit_user"
CONF_USER_ACTION = "user_action"
CONF_USER_ACTIVITY_MODE = "user_activity_mode"
CONF_ADVANCED_BASELINE = "advanced_profile_defaults"

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
            default=defaults.get(CONF_ALLOWED_USERS, DEFAULT_ALLOWED_USERS) if include_users else "",
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=sorted({*(known_users or []), *defaults.get(CONF_ALLOWED_USERS, DEFAULT_ALLOWED_USERS)}, key=str.lower) if include_users else [],
                multiple=include_users,
                custom_value=include_users,
                mode=selector.SelectSelectorMode.DROPDOWN if include_users else selector.SelectSelectorMode.DROPDOWN,
            )
        ) if include_users else str,
        vol.Required(
            CONF_ACTIVITY_FILTER,
            default=defaults.get(CONF_ACTIVITY_FILTER, DEFAULT_ACTIVITY_FILTER),
        ): vol.In(ACTIVITY_VALUES),
        vol.Required(
            CONF_UPDATE_PROFILE,
            default=defaults.get(CONF_UPDATE_PROFILE, DEFAULT_UPDATE_PROFILE),
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    selector.SelectOptionDict(value="extended", label="Extended"),
                    selector.SelectOptionDict(value="conservative", label="Conservative"),
                    selector.SelectOptionDict(value="balanced", label="Balanced"),
                    selector.SelectOptionDict(value="adaptive", label="Adaptive"),
                    selector.SelectOptionDict(value="custom", label="Advanced"),
                ],
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        ),
    }
    if known_users:
        fields[vol.Optional(CONF_EDIT_USER)] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=known_users,
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        )
    return vol.Schema(fields)


def _advanced_schema(defaults: dict, *, is_custom: bool) -> vol.Schema:
    fields = {
        vol.Optional(CONF_USER_AGENT): str,
        vol.Required(
            CONF_EXPOSE_DEBUG_ATTRIBUTES,
            default=defaults.get(CONF_EXPOSE_DEBUG_ATTRIBUTES, DEFAULT_EXPOSE_DEBUG_ATTRIBUTES),
        ): bool,
    }
    if is_custom:
        fields.update(
            {
                vol.Required(
                    CONF_UPDATE_INTERVAL,
                    default=defaults.get(CONF_UPDATE_INTERVAL, int(DEFAULT_UPDATE_INTERVAL.total_seconds())),
                ): vol.All(int, vol.Range(min=15)),
                vol.Required(
                    CONF_USE_GARMIN_TRACKPOINT_FREQUENCY,
                    default=defaults.get(CONF_USE_GARMIN_TRACKPOINT_FREQUENCY, DEFAULT_USE_GARMIN_TRACKPOINT_FREQUENCY),
                ): bool,
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
        )
    return vol.Schema(fields)


def _advanced_profile_schema(defaults: dict) -> vol.Schema:
    options = []
    if defaults.get("has_existing_advanced"):
        options.append(selector.SelectOptionDict(value="existing", label="Existing settings"))
    options.extend(
        [
            selector.SelectOptionDict(value="extended", label="Extended"),
            selector.SelectOptionDict(value="conservative", label="Conservative"),
            selector.SelectOptionDict(value="balanced", label="Balanced"),
            selector.SelectOptionDict(value="adaptive", label="Adaptive"),
        ]
    )
    return vol.Schema(
        {
            vol.Required(
                CONF_ADVANCED_BASELINE,
                default=defaults.get(CONF_ADVANCED_BASELINE, "conservative"),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=options,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
        }
    )


def _user_policy_schema(defaults: dict) -> vol.Schema:
    allowed_activities = defaults.get("allowed_activities", []) or []
    activity_mode = "custom" if allowed_activities else "inherit_global"
    return vol.Schema(
        {
            vol.Required(
                CONF_USER_ACTION,
                default=defaults.get(CONF_USER_ACTION, "update"),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(value="update", label="Update policy"),
                        selector.SelectOptionDict(value="remove", label="Remove user"),
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
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
    if include_users:
        raw_allowed = out.get(CONF_ALLOWED_USERS, [])
        if isinstance(raw_allowed, list):
            out[CONF_ALLOWED_USERS] = [str(u).strip() for u in raw_allowed if str(u).strip()]
        else:
            raw_users = str(raw_allowed or "")
            out[CONF_ALLOWED_USERS] = [u.strip() for u in raw_users.split(",") if u.strip()]
    else:
        out[CONF_ALLOWED_USERS] = []
    profile = str(out.get(CONF_UPDATE_PROFILE, DEFAULT_UPDATE_PROFILE) or DEFAULT_UPDATE_PROFILE).strip().lower()
    if profile not in UPDATE_PROFILE_VALUES:
        profile = DEFAULT_UPDATE_PROFILE
    out[CONF_UPDATE_PROFILE] = profile
    if CONF_USER_AGENT in out:
        user_agent = str(out.get(CONF_USER_AGENT, DEFAULT_USER_AGENT) or "").strip()
        out[CONF_USER_AGENT] = user_agent or DEFAULT_USER_AGENT
    out.pop(CONF_EDIT_USER, None)
    return out


def _normalize_advanced(inp: dict, *, is_custom: bool) -> dict:
    out = dict(inp)
    user_agent = str(out.get(CONF_USER_AGENT, DEFAULT_USER_AGENT) or "").strip()
    if not user_agent:
        user_agent = DEFAULT_USER_AGENT
    if len(user_agent) > 256:
        raise vol.Invalid(ERROR_INVALID_USER_AGENT)
    out[CONF_USER_AGENT] = user_agent
    if is_custom:
        out[CONF_UPDATE_INTERVAL] = int(out.get(CONF_UPDATE_INTERVAL, int(DEFAULT_UPDATE_INTERVAL.total_seconds())))
        out[CONF_USE_GARMIN_TRACKPOINT_FREQUENCY] = bool(out.get(CONF_USE_GARMIN_TRACKPOINT_FREQUENCY, DEFAULT_USE_GARMIN_TRACKPOINT_FREQUENCY))
        out[CONF_INITIAL_TRACKPOINT_WAIT] = int(out.get(CONF_INITIAL_TRACKPOINT_WAIT, int(DEFAULT_INITIAL_TRACKPOINT_WAIT.total_seconds() / 60)))
        out[CONF_MAX_RUNTIME_HOURS] = int(out.get(CONF_MAX_RUNTIME_HOURS, DEFAULT_MAX_RUNTIME_HOURS))
        out[CONF_STALE_MINUTES] = int(out.get(CONF_STALE_MINUTES, DEFAULT_STALE_MINUTES))
        out[CONF_FINALIZATION_MINUTES] = int(out.get(CONF_FINALIZATION_MINUTES, DEFAULT_FINALIZATION_MINUTES))
        out[CONF_RETAIN_ENDED_HOURS] = int(out.get(CONF_RETAIN_ENDED_HOURS, DEFAULT_RETAIN_ENDED_HOURS))
        out[CONF_DEFER_STARTUP_POLL_SECONDS] = int(out.get(CONF_DEFER_STARTUP_POLL_SECONDS, DEFAULT_DEFER_STARTUP_POLL_SECONDS))
    else:
        for key in (
            CONF_UPDATE_INTERVAL,
            CONF_USE_GARMIN_TRACKPOINT_FREQUENCY,
            CONF_INITIAL_TRACKPOINT_WAIT,
            CONF_MAX_RUNTIME_HOURS,
            CONF_STALE_MINUTES,
            CONF_FINALIZATION_MINUTES,
            CONF_RETAIN_ENDED_HOURS,
            CONF_DEFER_STARTUP_POLL_SECONDS,
        ):
            out.pop(key, None)
    return out


def _normalize_advanced_profile(inp: dict) -> dict:
    out = dict(inp)
    baseline = str(out.get(CONF_ADVANCED_BASELINE, "conservative") or "conservative").strip().lower()
    if baseline not in {"existing", "extended", "conservative", "balanced", "adaptive"}:
        baseline = "conservative"
    out[CONF_ADVANCED_BASELINE] = baseline
    return out


def _normalize_user_policy(inp: dict) -> dict:
    out = dict(inp)
    action = str(out.get(CONF_USER_ACTION, "update") or "update").strip().lower()
    if action not in {"update", "remove"}:
        action = "update"
    out[CONF_USER_ACTION] = action
    if action == "remove":
        out["allowed_activities"] = None
        out.pop(CONF_USER_ACTIVITY_MODE, None)
        return out
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
                {},
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

    def _runtime_manager(self):
        runtime = getattr(self._config_entry, "runtime_data", None)
        return getattr(runtime, "manager", None) if runtime is not None else None

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
        manager = self._runtime_manager()
        if manager is not None:
            for policy in getattr(manager, "known_users", {}).values():
                name = str(getattr(policy, "name", "") or "").strip()
                if name:
                    user_names.add(name)
        return sorted(user_names, key=str.lower)

    @staticmethod
    def _remove_user_from_options(defaults: dict, selected_user: str) -> dict:
        user_key = selected_user.strip().lower()
        merged = dict(defaults)
        current_users = [u for u in merged.get(CONF_ALLOWED_USERS, []) if isinstance(u, str)]
        merged[CONF_ALLOWED_USERS] = [u for u in current_users if u.strip().lower() != user_key]
        current_policies = dict(merged.get(CONF_USER_POLICIES, {}) or {})
        merged[CONF_USER_POLICIES] = {
            key: value
            for key, value in current_policies.items()
            if str(key).strip().lower() != user_key
        }
        return merged

    def _has_existing_advanced_settings(self, defaults: dict) -> bool:
        return bool(
            defaults.get(CONF_UPDATE_PROFILE) == "custom"
            or defaults.get(CONF_ADVANCED_BASELINE)
            or defaults.get(CONF_UPDATE_INTERVAL) is not None
            or defaults.get(CONF_USE_GARMIN_TRACKPOINT_FREQUENCY) is not None
            or defaults.get(CONF_INITIAL_TRACKPOINT_WAIT) is not None
            or defaults.get(CONF_STALE_MINUTES) is not None
        )

    async def async_step_init(self, user_input=None):
        defaults = {**self._config_entry.data, **self._config_entry.options}
        known_users = self._known_users()
        errors = {}
        if user_input is not None:
            try:
                normalized = _normalize(user_input, include_users=True)
                selected_user = str(user_input.get(CONF_EDIT_USER, "") or "").strip()
                merged = {**defaults, **normalized}
                if selected_user:
                    self._pending_options = merged
                    self._selected_user = selected_user
                    return await self.async_step_user_policy()
                if normalized.get(CONF_UPDATE_PROFILE) == "custom":
                    self._pending_options = merged
                    self._selected_user = None
                    return await self.async_step_advanced_profile()
                return self.async_create_entry(title="", data=merged)
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
                {},
            ),
            errors=errors,
        )

    async def async_step_advanced_profile(self, user_input=None):
        defaults = self._pending_options or {**self._config_entry.data, **self._config_entry.options}
        profile_defaults = {
            **defaults,
            "has_existing_advanced": self._has_existing_advanced_settings(defaults),
        }
        if profile_defaults["has_existing_advanced"]:
            profile_defaults[CONF_ADVANCED_BASELINE] = "existing"
        errors = {}
        if user_input is not None:
            try:
                normalized = _normalize_advanced_profile(user_input)
                merged = {**defaults, **normalized}
                self._pending_options = merged
                return await self.async_step_advanced()
            except vol.Invalid as err:
                errors["base"] = _error_key(err)
        return self.async_show_form(
            step_id="advanced_profile",
            data_schema=self.add_suggested_values_to_schema(
                _advanced_profile_schema(profile_defaults),
                {},
            ),
            errors=errors,
        )

    async def async_step_advanced(self, user_input=None):
        defaults = self._pending_options or {**self._config_entry.data, **self._config_entry.options}
        is_custom = str(defaults.get(CONF_UPDATE_PROFILE, DEFAULT_UPDATE_PROFILE) or DEFAULT_UPDATE_PROFILE).strip().lower() == "custom"
        saved_baseline = str((self._config_entry.options or {}).get(CONF_ADVANCED_BASELINE, "conservative") or "conservative").strip().lower()
        selected_baseline = str(defaults.get(CONF_ADVANCED_BASELINE, saved_baseline) or saved_baseline).strip().lower()
        baseline_defaults = {
            CONF_UPDATE_INTERVAL: {"extended": 600, "conservative": 60, "balanced": 30, "adaptive": 15}.get(selected_baseline, 60),
            CONF_USE_GARMIN_TRACKPOINT_FREQUENCY: selected_baseline == "adaptive",
            CONF_INITIAL_TRACKPOINT_WAIT: {"extended": 20, "conservative": 10, "balanced": 10, "adaptive": 10}.get(selected_baseline, 10),
            CONF_STALE_MINUTES: {"extended": 30, "conservative": 15, "balanced": 15, "adaptive": 15}.get(selected_baseline, 15),
        }
        effective_defaults = dict(defaults)
        if is_custom:
            if selected_baseline == "existing":
                effective_defaults.setdefault(CONF_UPDATE_INTERVAL, 60)
                effective_defaults.setdefault(CONF_USE_GARMIN_TRACKPOINT_FREQUENCY, False)
                effective_defaults.setdefault(CONF_INITIAL_TRACKPOINT_WAIT, 10)
                effective_defaults.setdefault(CONF_STALE_MINUTES, 15)
            elif selected_baseline != saved_baseline:
                effective_defaults.update(baseline_defaults)
            else:
                effective_defaults.setdefault(CONF_UPDATE_INTERVAL, baseline_defaults[CONF_UPDATE_INTERVAL])
                effective_defaults.setdefault(CONF_USE_GARMIN_TRACKPOINT_FREQUENCY, baseline_defaults[CONF_USE_GARMIN_TRACKPOINT_FREQUENCY])
                effective_defaults.setdefault(CONF_INITIAL_TRACKPOINT_WAIT, baseline_defaults[CONF_INITIAL_TRACKPOINT_WAIT])
                effective_defaults.setdefault(CONF_STALE_MINUTES, baseline_defaults[CONF_STALE_MINUTES])
        errors = {}
        if user_input is not None:
            try:
                normalized = _normalize_advanced(user_input, is_custom=is_custom)
                merged = {**defaults, **normalized}
                if self._selected_user:
                    self._pending_options = merged
                    return await self.async_step_user_policy()
                return self.async_create_entry(title="", data=merged)
            except vol.Invalid as err:
                errors["base"] = _error_key(err)
        return self.async_show_form(
            step_id="advanced",
            data_schema=self.add_suggested_values_to_schema(
                _advanced_schema(effective_defaults, is_custom=is_custom),
                {
                    CONF_USER_AGENT: effective_defaults.get(CONF_USER_AGENT, DEFAULT_USER_AGENT),
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
        if not existing:
            manager = self._runtime_manager()
            if manager is not None:
                policy = getattr(manager, "known_users", {}).get(selected_user.lower())
                if policy is not None:
                    existing = {
                        "enabled": bool(getattr(policy, "enabled", True)),
                        "mode": str(getattr(policy, "mode", "normal") or "normal"),
                        "allowed_activities": list(getattr(policy, "allowed_activities", []) or []),
                    }
        existing.setdefault(CONF_USER_ACTION, "update")
        errors = {}
        if user_input is not None:
            try:
                normalized_policy = _normalize_user_policy(user_input)
                action = normalized_policy.pop(CONF_USER_ACTION, "update")
                if action == "remove":
                    manager = self._runtime_manager()
                    if manager is not None:
                        await manager.async_remove_user(selected_user)
                    merged = self._remove_user_from_options(defaults, selected_user)
                    return self.async_create_entry(title="", data=merged)
                normalized_policy["name"] = selected_user
                user_policies = {
                    key: value
                    for key, value in user_policies.items()
                    if str(key).strip().lower() != selected_user.lower()
                }
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
