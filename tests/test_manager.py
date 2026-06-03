import pytest
from datetime import UTC, datetime

from custom_components.garmin_livetrack.coordinator import GarminLiveTrackManager
from custom_components.garmin_livetrack.binary_sensor import GarminAnyActiveBinarySensor
from custom_components.garmin_livetrack.models import (
    LiveTrackIdentity,
    LiveTrackPoint,
    LiveTrackSession,
    LiveTrackSource,
    LiveTrackStatus,
)
from custom_components.garmin_livetrack.sensor import GarminUserStatusSensor


class DummyStore:
    def __init__(self): self.data = {}
    async def async_load(self): return self.data
    async def async_save(self, data): self.data = data


class DummyFetch:
    def __init__(self):
        self.ok = True
        self.session = {"sessionId": "abc", "userDisplayName": "Runner", "activityType": "running"}
        self.source = {"trackpoints_source": "api_or_payload"}
        self.trackpoint_count = 1
        self.last_trackpoint = {
            "dateTime": "2026-01-01T00:00:00Z",
            "position": {"lat": 55.67, "lon": 12.56},
        }
        self.fetched_at = datetime.now(UTC)
        self.errors = []
        self.page_status = 200
        self.api_status = 200


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


@pytest.mark.asyncio
async def test_case_insensitive_user_policy_lookup(hass):
    m = GarminLiveTrackManager(hass, DummyClient(), DummyStore(), {"activity_filter": "running"})
    await m.async_setup()
    ok = await m.async_set_user_policy("TeeJay", allowed_activities=["walking", "cycling"])
    assert ok is True
    assert m._effective_activity_filter("teejay") == ["cycling", "walking"]
    assert m._effective_activity_filter("TEEJAY") == ["cycling", "walking"]
    users = await m.async_list_users()
    assert users[0]["name"] == "TeeJay"
    assert users[0]["activity_policy_mode"] == "custom"


@pytest.mark.asyncio
async def test_user_status_sensor_retains_ended_session_summary(hass):
    m = GarminLiveTrackManager(hass, DummyClient(), DummyStore(), {})
    await m.async_setup()
    identity = LiveTrackIdentity(
        "ended-session",
        "token",
        "https://livetrack.garmin.com/session/ended-session/token/token",
        "https://livetrack.garmin.com/session/ended-session/token/t...n",
        LiveTrackSource.SERVICE,
    )
    session = LiveTrackSession(
        identity=identity,
        garmin_user="Runner",
        activity_type="running",
        start=datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
        expected_end=datetime(2026, 1, 1, 11, 0, tzinfo=UTC),
        actual_end=datetime(2026, 1, 1, 11, 5, tzinfo=UTC),
        first_seen=datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
        last_fetch=datetime(2026, 1, 1, 11, 5, tzinfo=UTC),
        last_success=datetime(2026, 1, 1, 11, 5, tzinfo=UTC),
        last_point=LiveTrackPoint(
            timestamp=datetime(2026, 1, 1, 11, 4, tzinfo=UTC),
            distance_m=12345,
            duration_s=3600,
            speed_mps=3.5,
            altitude_m=42,
            heart_rate_bpm=150,
            power_w=220,
        ),
        trackpoint_count=123,
        status=LiveTrackStatus.ENDED,
    )
    m.ended_sessions["ended-session"] = session

    entity = GarminUserStatusSensor(m, "runner")
    assert entity.available is True
    assert entity.native_value == "ended"
    attrs = entity.extra_state_attributes
    assert attrs["garmin_user"] == "Runner"
    assert attrs["activity"] == "running"
    assert attrs["distance_km"] == 12.345
    assert attrs["duration_min"] == 60.0
    assert attrs["heart_rate_bpm"] == 150
    assert attrs["activity_icon"] == "mdi:run"
    assert attrs["status_icon"] == "mdi:check-circle-outline"


@pytest.mark.asyncio
async def test_any_active_binary_sensor_exposes_aggregate_attributes(hass):
    m = GarminLiveTrackManager(hass, DummyClient(), DummyStore(), {})
    await m.async_setup()
    await m.async_add_url("https://livetrack.garmin.com/session/abc/token/def", LiveTrackSource.SERVICE)

    entity = GarminAnyActiveBinarySensor(m)
    assert entity.is_on is True
    attrs = entity.extra_state_attributes
    assert attrs["active_count"] == 1
    assert attrs["active_users"] == ["Runner"]
    assert attrs["active_activities"] == ["running"]
    assert attrs["active_summaries"][0]["status"] == "active"
