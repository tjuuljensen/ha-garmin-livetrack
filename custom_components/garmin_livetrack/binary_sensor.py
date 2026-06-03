from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity

from .coordinator import ACTIVE_STATES
from .icons import activity_icon
from .models import stable_session_hash
from .sensor import _device_info, _discover_entity_keys, _entity_label, _integration_device_info, _select_session_for_user


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


class GarminUserActiveBinarySensor(_BaseBinary):
    def __init__(self, manager, entity_key: str):
        super().__init__(manager)
        self.entity_key = entity_key
        self._attr_unique_id = f"garmin_livetrack_user_active_{stable_session_hash(entity_key)}"

    @property
    def name(self):
        coord = _select_session_for_user(self.manager, self.entity_key)
        return f"Garmin LiveTrack {_entity_label(self.entity_key, coord)} Active"

    @property
    def device_info(self):
        coord = _select_session_for_user(self.manager, self.entity_key)
        return _device_info(self.entity_key, coord)

    @property
    def is_on(self):
        coord = _select_session_for_user(self.manager, self.entity_key)
        return bool(coord and coord.session.status in ACTIVE_STATES)

    @property
    def icon(self):
        coord = _select_session_for_user(self.manager, self.entity_key)
        if not coord:
            return "mdi:map-marker-path"
        return activity_icon(coord.session.activity_type, self.is_on)
