from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .models import stable_session_hash


def _user_key(name: str | None) -> str:
    value = (name or "").strip().lower()
    return value


def _integration_device_info() -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, "integration")},
        name="Garmin LiveTrack",
        manufacturer="Garmin",
        model="LiveTrack Integration",
    )


def _select_session_for_user(manager, key: str):
    candidates = []
    for sid, coord in manager.sessions.items():
        session = coord.session
        if key.startswith("session:"):
            if sid == key.split(":", 1)[1]:
                candidates.append(coord)
            continue
        if _user_key(session.garmin_user) == key:
            candidates.append(coord)
    if not candidates:
        return None
    candidates.sort(
        key=lambda c: (
            c.session.last_success is not None,
            c.session.last_success or c.session.first_seen,
        ),
        reverse=True,
    )
    return candidates[0]


def _discover_entity_keys(manager) -> set[str]:
    keys: set[str] = set()
    for name in manager.known_users:
        key = _user_key(name)
        if key:
            keys.add(key)
    for sid, coord in manager.sessions.items():
        user = (coord.session.garmin_user or "").strip()
        if user:
            keys.add(_user_key(user))
        else:
            keys.add(f"session:{sid}")
    return keys


def _entity_label(entity_key: str, coord) -> str:
    user = ((coord.session.garmin_user if coord else "") or "").strip()
    if user:
        return user
    if entity_key.startswith("session:"):
        return f"Session {stable_session_hash(entity_key.split(':', 1)[1])[:8]}"
    return entity_key


def _device_info(entity_key: str, coord) -> DeviceInfo:
    label = _entity_label(entity_key, coord)
    if entity_key.startswith("session:"):
        sid = entity_key.split(":", 1)[1]
        return DeviceInfo(
            identifiers={(DOMAIN, f"session:{sid}")},
            name=f"Garmin LiveTrack {label}",
            manufacturer="Garmin",
            model="LiveTrack Session",
        )
    return DeviceInfo(
        identifiers={(DOMAIN, f"user:{stable_session_hash(entity_key)}")},
        name=f"Garmin LiveTrack {label}",
        manufacturer="Garmin",
        model="LiveTrack User",
    )


async def async_setup_entry(hass, entry, async_add_entities):
    manager = entry.runtime_data.manager
    known: dict[str, GarminUserStatusSensor] = {}
    async_add_entities(
        [
            GarminActiveCountSensor(manager),
            GarminLastErrorSensor(manager),
            GarminSessionCountSensor(manager),
        ]
    )

    def _sync() -> None:
        new_entities = []
        for key in _discover_entity_keys(manager):
            if key in known:
                continue
            entity = GarminUserStatusSensor(manager, key)
            known[key] = entity
            new_entities.append(entity)
        if new_entities:
            async_add_entities(new_entities)

    manager.async_add_listener(_sync)
    _sync()


class _BaseManagerSensor(SensorEntity):
    def __init__(self, manager):
        self.manager = manager
        self._unsub = None

    async def async_added_to_hass(self):
        self._unsub = self.manager.async_add_listener(self.async_write_ha_state)

    async def async_will_remove_from_hass(self):
        if self._unsub:
            self._unsub()


class GarminActiveCountSensor(_BaseManagerSensor):
    _attr_name = "Garmin LiveTrack Active Count"
    _attr_unique_id = "garmin_livetrack_active_count"

    @property
    def device_info(self):
        return _integration_device_info()

    @property
    def native_value(self):
        return len(self.manager.sessions)


class GarminSessionCountSensor(_BaseManagerSensor):
    _attr_name = "Garmin LiveTrack Session Count"
    _attr_unique_id = "garmin_livetrack_session_count"

    @property
    def device_info(self):
        return _integration_device_info()

    @property
    def native_value(self):
        return len(self.manager.sessions) + len(self.manager.ended_sessions)


class GarminLastErrorSensor(_BaseManagerSensor):
    _attr_name = "Garmin LiveTrack Last Error"
    _attr_unique_id = "garmin_livetrack_last_error"

    @property
    def device_info(self):
        return _integration_device_info()

    @property
    def native_value(self):
        return self.manager.last_error or "none"


class GarminUserStatusSensor(_BaseManagerSensor):
    def __init__(self, manager, entity_key: str):
        super().__init__(manager)
        self.entity_key = entity_key
        hash_part = stable_session_hash(entity_key)
        self._attr_unique_id = f"garmin_livetrack_user_status_{hash_part}"

    @property
    def name(self):
        coord = _select_session_for_user(self.manager, self.entity_key)
        return f"Garmin LiveTrack {_entity_label(self.entity_key, coord)} Status"

    @property
    def device_info(self):
        coord = _select_session_for_user(self.manager, self.entity_key)
        return _device_info(self.entity_key, coord)

    @property
    def available(self):
        return _select_session_for_user(self.manager, self.entity_key) is not None

    @property
    def native_value(self):
        coord = _select_session_for_user(self.manager, self.entity_key)
        if not coord:
            return "ended"
        return coord.session.status.value

    @property
    def extra_state_attributes(self):
        coord = _select_session_for_user(self.manager, self.entity_key)
        if not coord:
            return {"entity_key": self.entity_key}
        s = coord.session
        return {
            "entity_key": self.entity_key,
            "session_id_hash": stable_session_hash(s.identity.session_id),
            "url": s.identity.canonical_url,
            "garmin_user": s.garmin_user,
            "activity": s.activity_type,
            "trackpoint_count": s.trackpoint_count,
            "last_fetch": s.last_fetch.isoformat() if s.last_fetch else None,
            "last_success": s.last_success.isoformat() if s.last_success else None,
            "page_status": coord.last_page_status,
            "api_status": coord.last_api_status,
            "trackpoints_source": coord.last_source_branch,
            "poll_task_alive": bool(coord._task and not coord._task.done()),
        }

    @property
    def icon(self):
        coord = _select_session_for_user(self.manager, self.entity_key)
        if not coord:
            return "mdi:progress-question"
        status = coord.session.status
        if status.value == "fetching":
            return "mdi:cloud-sync-outline"
        if status.value == "waiting_for_trackpoint":
            return "mdi:timer-sand"
        if status.value == "active":
            return "mdi:signal"
        if status.value == "ending":
            return "mdi:flag-checkered"
        if status.value == "ended":
            return "mdi:check-circle-outline"
        if status.value in {"stale", "garmin_error"}:
            return "mdi:alert-circle-outline"
        if status.value in {"expired", "stopped"}:
            return "mdi:stop-circle-outline"
        return "mdi:progress-question"
