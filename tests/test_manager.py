import pytest
from datetime import UTC, datetime

from custom_components.garmin_livetrack.coordinator import GarminLiveTrackManager
from custom_components.garmin_livetrack.models import LiveTrackIdentity, LiveTrackSource


class DummyStore:
    def __init__(self): self.data = {}
    async def async_load(self): return self.data
    async def async_save(self, data): self.data = data


class DummyFetch:
    def __init__(self):
        self.ok = True
        self.session = {"sessionId": "abc", "userDisplayName": "Runner", "activityType": "running"}
        self.trackpoint_count = 1
        self.fetched_at = datetime.now(UTC)
        self.errors = []


class DummyClient:
    def parse_livetrack_identity(self, **kwargs):
        return LiveTrackIdentity("abc", "def", "https://livetrack.garmin.com/session/abc/token/def", "https://livetrack.garmin.com/session/abc/token/d...f", kwargs.get("source"))
    async def fetch(self, identity): return DummyFetch()


@pytest.mark.asyncio
async def test_add_and_duplicate(hass):
    m = GarminLiveTrackManager(hass, DummyClient(), DummyStore(), {})
    await m.async_setup()
    r1 = await m.async_add_url("https://livetrack.garmin.com/session/abc/token/def", LiveTrackSource.SERVICE)
    r2 = await m.async_add_url("https://livetrack.garmin.com/session/abc/token/def", LiveTrackSource.SERVICE)
    assert r1.ok
    assert r2.status.value == "duplicate"