from homeassistant.components.binary_sensor import BinarySensorEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([GarminAnyActiveBinarySensor(entry.runtime_data.manager)])


class GarminAnyActiveBinarySensor(BinarySensorEntity):
    _attr_name = "Garmin LiveTrack Any Active"
    _attr_unique_id = "garmin_livetrack_any_active"

    def __init__(self, manager):
        self.manager = manager

    @property
    def is_on(self):
        return bool(self.manager.sessions)