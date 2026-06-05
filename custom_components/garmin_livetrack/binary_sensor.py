from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity

from .coordinator import ACTIVE_STATES
from .icons import activity_icon
from .models import (
    distance_km_from_m,
    duration_hms_from_seconds,
    pace_min_km_from_speed_mps,
    speed_kmh_from_mps,
    stable_session_hash,
)
from .sensor import _SessionWrapper, _device_info, _discover_entity_keys, _entity_label, _integration_device_info, _select_session_snapshot, _select_session_for_user


async def async_setup_entry(hass, entry, async_add_entities):
    manager = entry.runtime_data.manager
    known: dict[str, GarminUserActiveBinarySensor] = {}
    async_add_entities([GarminAnyActiveBinarySensor(manager)])

    def _sync() -> None:
        new_entities = []
        for key in _discover_entity_keys(manager):
            if key in known:
                continue
            entity = GarminUserActiveBinarySensor(manager, key)
            known[key] = entity
            new_entities.append(entity)
        if new_entities:
            async_add_entities(new_entities)

    manager.async_add_listener(_sync)
    _sync()


class _BaseBinary(BinarySensorEntity):
    def __init__(self, manager):
        self.manager = manager
        self._unsub = None

    async def async_added_to_hass(self):
        self._unsub = self.manager.async_add_listener(self.async_write_ha_state)

    async def async_will_remove_from_hass(self):
        if self._unsub:
            self._unsub()


class GarminAnyActiveBinarySensor(_BaseBinary):
    _attr_name = "Garmin LiveTrack Any Active"
    _attr_unique_id = "garmin_livetrack_any_active"

    @property
    def device_info(self):
        return _integration_device_info()

    @property
    def is_on(self):
        return any(c.session.status in ACTIVE_STATES for c in self.manager.sessions.values())

    @property
    def extra_state_attributes(self):
        active = [
            c for c in self.manager.sessions.values()
            if c.session.status in ACTIVE_STATES
        ]
        active.sort(
            key=lambda c: (
                c.session.last_success is not None,
                c.session.last_success or c.session.first_seen,
            ),
            reverse=True,
        )
        summaries = [
            {
                "user": c.session.garmin_user or f"session:{stable_session_hash(c.session.identity.session_id)[:8]}",
                "activity": c.session.activity_type or "other",
                "activity_type": c.session.activity_type or "other",
                "activity_type_raw": c.session.activity_type_raw,
                "activity_icon": activity_icon(c.session.activity_type, True),
                "status": c.session.status.value,
                "source": c.session.identity.source.value,
                "session_id_hash": stable_session_hash(c.session.identity.session_id),
                "speed_kmh": speed_kmh_from_mps(c.session.last_point.speed_mps) if c.session.last_point else None,
                "pace_min_km": pace_min_km_from_speed_mps(c.session.last_point.speed_mps) if c.session.last_point else None,
                "distance_km": distance_km_from_m(c.session.last_point.distance_m) if c.session.last_point else None,
                "duration_hms": duration_hms_from_seconds(c.session.last_point.duration_s) if c.session.last_point else None,
            }
            for c in active
        ]
        return {
            "active_count": len(active),
            "active_users": [item["user"] for item in summaries],
            "active_activities": [item["activity"] for item in summaries],
            "active_summaries": summaries,
        }

    @property
    def icon(self):
        active = [
            c for c in self.manager.sessions.values()
            if c.session.status in ACTIVE_STATES
        ]
        if not active:
            return "mdi:map-marker-path"
        if len(active) == 1:
            coord = active[0]
            return activity_icon(coord.session.activity_type, True)
        return "mdi:map-marker-multiple"


class GarminUserActiveBinarySensor(_BaseBinary):
    def __init__(self, manager, entity_key: str):
        super().__init__(manager)
        self.entity_key = entity_key
        self._attr_unique_id = f"garmin_livetrack_user_active_{stable_session_hash(entity_key)}"

    @property
    def name(self):
        session, coord = _select_session_snapshot(self.manager, self.entity_key)
        wrapped = coord if coord is not None else (_SessionWrapper(session) if session is not None else None)
        return f"Garmin LiveTrack {_entity_label(self.entity_key, wrapped)} Active"

    @property
    def device_info(self):
        session, coord = _select_session_snapshot(self.manager, self.entity_key)
        if coord is not None:
            return _device_info(self.entity_key, coord)
        if session is not None:
            return _device_info(self.entity_key, _SessionWrapper(session))
        return _device_info(self.entity_key, None)

    @property
    def is_on(self):
        coord = _select_session_for_user(self.manager, self.entity_key)
        return bool(coord and coord.session.status in ACTIVE_STATES)

    @property
    def icon(self):
        session, coord = _select_session_snapshot(self.manager, self.entity_key)
        if not session:
            return "mdi:map-marker-path"
        s = coord.session if coord else session
        return activity_icon(s.activity_type, s.status in ACTIVE_STATES)
