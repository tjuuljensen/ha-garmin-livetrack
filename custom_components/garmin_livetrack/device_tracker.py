from __future__ import annotations

from homeassistant.components.device_tracker import SourceType
from homeassistant.components.device_tracker.config_entry import TrackerEntity

from .models import stable_session_hash


async def async_setup_entry(hass, entry, async_add_entities):
    manager = entry.runtime_data.manager
    known: dict[str, GarminSessionTracker] = {}

    def _sync() -> None:
        new_entities = []
        for sid in manager.sessions:
            if sid in known:
                continue
            entity = GarminSessionTracker(manager, sid)
            known[sid] = entity
            new_entities.append(entity)
        if new_entities:
            async_add_entities(new_entities)

    manager.async_add_listener(_sync)
    _sync()


class GarminSessionTracker(TrackerEntity):
    def __init__(self, manager, session_id: str):
        self.manager = manager
        self.session_id = session_id
        self._unsub = None
        sid_hash = stable_session_hash(session_id)
        self._attr_name = f"Garmin LiveTrack {sid_hash}"
        self._attr_unique_id = f"garmin_livetrack_tracker_{sid_hash}"

    async def async_added_to_hass(self):
        self._unsub = self.manager.async_add_listener(self.async_write_ha_state)

    async def async_will_remove_from_hass(self):
        if self._unsub:
            self._unsub()

    @property
    def source_type(self):
        return SourceType.GPS

    @property
    def latitude(self):
        coord = self.manager.sessions.get(self.session_id)
        point = coord.session.last_point if coord else None
        return point.latitude if point else None

    @property
    def longitude(self):
        coord = self.manager.sessions.get(self.session_id)
        point = coord.session.last_point if coord else None
        return point.longitude if point else None

    @property
    def location_accuracy(self):
        return 20

    @property
    def extra_state_attributes(self):
        coord = self.manager.sessions.get(self.session_id)
        if not coord:
            return {"session_id_hash": stable_session_hash(self.session_id)}
        s = coord.session
        p = s.last_point
        return {
            "session_id_hash": stable_session_hash(self.session_id),
            "activity": s.activity_type,
            "garmin_user": s.garmin_user,
            "status": s.status.value,
            "altitude": p.altitude_m if p else None,
            "speed": p.speed_mps if p else None,
            "last_trackpoint_time": p.timestamp.isoformat() if p and p.timestamp else None,
        }
