from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity

from .coordinator import ACTIVE_STATES
from .models import stable_session_hash


async def async_setup_entry(hass, entry, async_add_entities):
    manager = entry.runtime_data.manager
    known: dict[str, GarminSessionActiveBinarySensor] = {}
    async_add_entities([GarminAnyActiveBinarySensor(manager)])

    def _sync() -> None:
        new_entities = []
        for sid in manager.sessions:
            if sid in known:
                continue
            entity = GarminSessionActiveBinarySensor(manager, sid)
            known[sid] = entity
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
    def is_on(self):
        return any(c.session.status in ACTIVE_STATES for c in self.manager.sessions.values())


class GarminSessionActiveBinarySensor(_BaseBinary):
    def __init__(self, manager, session_id: str):
        super().__init__(manager)
        self.session_id = session_id
        sid_hash = stable_session_hash(session_id)
        self._attr_name = f"Garmin LiveTrack {sid_hash} Active"
        self._attr_unique_id = f"garmin_livetrack_active_{sid_hash}"

    @property
    def is_on(self):
        coord = self.manager.sessions.get(self.session_id)
        return bool(coord and coord.session.status in ACTIVE_STATES)
