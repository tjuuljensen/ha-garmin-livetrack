from __future__ import annotations

from homeassistant.components.device_tracker import SourceType
from homeassistant.components.device_tracker.config_entry import TrackerEntity

from .coordinator import ACTIVE_STATES
from .icons import activity_icon
from .models import stable_session_hash
from .sensor import _discover_entity_keys, _select_session_for_user


async def async_setup_entry(hass, entry, async_add_entities):
    manager = entry.runtime_data.manager
    known: dict[str, GarminUserTracker] = {}

    def _sync() -> None:
        new_entities = []
        for key in _discover_entity_keys(manager):
            if key in known:
                continue
            entity = GarminUserTracker(manager, key)
            known[key] = entity
            new_entities.append(entity)
        if new_entities:
            async_add_entities(new_entities)

    manager.async_add_listener(_sync)
    _sync()


class GarminUserTracker(TrackerEntity):
    def __init__(self, manager, entity_key: str):
        self.manager = manager
        self.entity_key = entity_key
        self._unsub = None
        self._attr_unique_id = f"garmin_livetrack_user_tracker_{stable_session_hash(entity_key)}"

    async def async_added_to_hass(self):
        self._unsub = self.manager.async_add_listener(self.async_write_ha_state)

    async def async_will_remove_from_hass(self):
        if self._unsub:
            self._unsub()

    @property
    def name(self):
        coord = _select_session_for_user(self.manager, self.entity_key)
        user = (coord.session.garmin_user if coord else "") or ""
        user = user.strip()
        return f"Garmin LiveTrack {(user or self.entity_key)}"

    @property
    def source_type(self):
        return SourceType.GPS

    @property
    def latitude(self):
        coord = _select_session_for_user(self.manager, self.entity_key)
        point = coord.session.last_point if coord else None
        return point.latitude if point else None

    @property
    def longitude(self):
        coord = _select_session_for_user(self.manager, self.entity_key)
        point = coord.session.last_point if coord else None
        return point.longitude if point else None

    @property
    def location_accuracy(self):
        return 20

    @property
    def extra_state_attributes(self):
        coord = _select_session_for_user(self.manager, self.entity_key)
        if not coord:
            return {"entity_key": self.entity_key}
        s = coord.session
        p = s.last_point
        return {
            "entity_key": self.entity_key,
            "session_id_hash": stable_session_hash(s.identity.session_id),
            "activity": s.activity_type,
            "garmin_user": s.garmin_user,
            "status": s.status.value,
            "altitude": p.altitude_m if p else None,
            "speed": p.speed_mps if p else None,
            "last_trackpoint_time": p.timestamp.isoformat() if p and p.timestamp else None,
        }

    @property
    def icon(self):
        coord = _select_session_for_user(self.manager, self.entity_key)
        if not coord:
            return "mdi:map-marker-path"
        is_active = coord.session.status in ACTIVE_STATES
        return activity_icon(coord.session.activity_type, is_active)
