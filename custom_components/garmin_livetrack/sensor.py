from homeassistant.components.sensor import SensorEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([
        GarminActiveCountSensor(entry.runtime_data.manager),
        GarminLastErrorSensor(entry.runtime_data.manager),
    ])


class GarminActiveCountSensor(SensorEntity):
    _attr_name = "Garmin LiveTrack Active Count"
    _attr_unique_id = "garmin_livetrack_active_count"

    def __init__(self, manager):
        self.manager = manager

    @property
    def native_value(self):
        return len(self.manager.sessions)


class GarminLastErrorSensor(SensorEntity):
    _attr_name = "Garmin LiveTrack Last Error"
    _attr_unique_id = "garmin_livetrack_last_error"

    def __init__(self, manager):
        self.manager = manager

    @property
    def native_value(self):
        return self.manager.last_error