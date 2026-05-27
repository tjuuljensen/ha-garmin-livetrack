from __future__ import annotations

from homeassistant.components.sensor import SensorEntity

from .models import stable_session_hash


async def async_setup_entry(hass, entry, async_add_entities):
    manager = entry.runtime_data.manager
    known: dict[str, GarminSessionStatusSensor] = {}
    async_add_entities(
        [
            GarminActiveCountSensor(manager),
            GarminLastErrorSensor(manager),
            GarminSessionCountSensor(manager),
        ]
    )

    def _sync() -> None:
        new_entities = []
        for sid, coord in manager.sessions.items():
            if sid in known:
                continue
            entity = GarminSessionStatusSensor(manager, sid)
            known[sid] = entity
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
    def native_value(self):
        return len(self.manager.sessions)


class GarminSessionCountSensor(_BaseManagerSensor):
    _attr_name = "Garmin LiveTrack Session Count"
    _attr_unique_id = "garmin_livetrack_session_count"

    @property
    def native_value(self):
        return len(self.manager.sessions) + len(self.manager.ended_sessions)


class GarminLastErrorSensor(_BaseManagerSensor):
    _attr_name = "Garmin LiveTrack Last Error"
    _attr_unique_id = "garmin_livetrack_last_error"

    @property
    def native_value(self):
        return self.manager.last_error or "none"


class GarminSessionStatusSensor(_BaseManagerSensor):
    def __init__(self, manager, session_id: str):
        super().__init__(manager)
        self.session_id = session_id
        sid_hash = stable_session_hash(session_id)
        self._attr_name = f"Garmin LiveTrack {sid_hash} Status"
        self._attr_unique_id = f"garmin_livetrack_status_{sid_hash}"

    @property
    def available(self):
        return self.session_id in self.manager.sessions

    @property
    def native_value(self):
        coord = self.manager.sessions.get(self.session_id)
        if not coord:
            return "ended"
        return coord.session.status.value

    @property
    def extra_state_attributes(self):
        coord = self.manager.sessions.get(self.session_id)
        if not coord:
            return {"session_id_hash": stable_session_hash(self.session_id)}
        s = coord.session
        return {
            "session_id_hash": stable_session_hash(self.session_id),
            "redacted_url": s.identity.redacted_url,
            "garmin_user": s.garmin_user,
            "activity": s.activity_type,
            "trackpoint_count": s.trackpoint_count,
            "last_success": s.last_success.isoformat() if s.last_success else None,
        }
