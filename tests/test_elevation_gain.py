"""Optional Garmin elevation gain, independent of current altitude.

Payloads are synthetic examples of fields used by Garmin's current frontend;
the real session inspected during development did not contain gain fields.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from custom_components.garmin_livetrack.client import GarminLiveTrackClient
from custom_components.garmin_livetrack.const import EVENT_POINT_RECEIVED
from custom_components.garmin_livetrack.coordinator import GarminLiveTrackManager, LiveTrackSessionCoordinator
from custom_components.garmin_livetrack.device_tracker import GarminUserTracker
from custom_components.garmin_livetrack.models import LiveTrackIdentity, LiveTrackSession, LiveTrackSource, LiveTrackStatus
from custom_components.garmin_livetrack.sensor import GarminUserStatusSensor


class MemoryStore:
    def __init__(self):
        self.data = {}

    async def async_load(self):
        return self.data

    async def async_save(self, data):
        self.data = data


@pytest.fixture
def coordinator(hass):
    manager = GarminLiveTrackManager(hass, SimpleNamespace(), MemoryStore(), {})
    now = datetime.now(UTC)
    session = LiveTrackSession(
        identity=LiveTrackIdentity("gain", "test", "", "", LiveTrackSource.SERVICE),
        garmin_user="Runner",
        activity_type="running",
        start=now - timedelta(hours=1),
        expected_end=None,
        actual_end=None,
        first_seen=now,
        last_fetch=None,
        last_success=None,
        last_point=None,
        trackpoint_count=0,
        status=LiveTrackStatus.ACTIVE,
    )
    coord = LiveTrackSessionCoordinator(manager, session)
    manager.sessions["gain"] = coord
    return coord


@pytest.mark.parametrize(
    ("metrics", "expected"),
    [
        ({"elevationGainMeters": 12.5}, 12.5),
        ({"totalElevationGainMeters": 45, "elevationGainMeters": 12.5}, 45),
        ({"totalElevationGainMeters": 0, "elevationGainMeters": 12.5}, 0),
        ({"totalElevationGainMeters": None, "elevationGainMeters": 12.5}, 12.5),
        ({"fitnessPointData": {"elevationGainMeters": 12.5}}, 12.5),
        ({"elevationGainMeters": "12.5"}, 12.5),
        ({"elevationGainMeters": "invalid"}, None),
        ({"elevationGainMeters": None}, None),
        ({}, None),
    ],
)
def test_gain_fields_from_rest_trackpoint(coordinator, metrics, expected):
    raw = {
        "dateTime": "2026-01-01T10:00:00Z",
        "position": {"lat": 0, "lon": 0},
        "altitude": 125,
        **metrics,
    }
    client = GarminLiveTrackClient(hass=None)
    points = client._find_trackpoints({"trackPoints": [raw]})
    point = coordinator._to_point_sequence(points, None)
    assert point.elevation_gain_m == expected
    assert point.altitude_m == 125


def test_sparse_points_retain_gain_but_explicit_zero_replaces_it(coordinator):
    previous = coordinator._to_point({"altitude": 100, "elevationGainMeters": 25})
    sparse = coordinator._to_point_sequence([{"altitude": 200}], previous)
    assert sparse.elevation_gain_m == 25
    reset = coordinator._to_point_sequence([{"elevationGainMeters": 0}], sparse)
    assert reset.elevation_gain_m == 0
    assert coordinator._to_point_sequence([{"altitude": 200}], None).elevation_gain_m is None


@pytest.mark.parametrize("gain", [None, 0, 42.5])
def test_gain_in_entities_events_and_reused_point(coordinator, hass, gain):
    point = coordinator._to_point({"altitude": 125, "totalElevationGainMeters": gain})
    coordinator.session.last_point = point
    manager = coordinator.manager
    assert GarminUserStatusSensor(manager, "runner").extra_state_attributes["elevation_gain_m"] == gain
    assert GarminUserTracker(manager, "runner").extra_state_attributes["elevation_gain_m"] == gain
    manager._fire_point_received(coordinator.session)
    event = hass.bus.fired[-1]
    assert event["event_type"] == EVENT_POINT_RECEIVED
    assert event["event_data"]["elevation_gain_m"] == gain
    assert event["event_data"]["altitude_m"] == 125
    reused = coordinator._to_point(coordinator._point_to_fetch_payload(point))
    assert reused.elevation_gain_m == gain


def test_no_point_has_unknown_gain(coordinator):
    manager = coordinator.manager
    assert GarminUserStatusSensor(manager, "runner").extra_state_attributes["elevation_gain_m"] is None
    assert GarminUserTracker(manager, "runner").extra_state_attributes["elevation_gain_m"] is None
    assert manager._session_event_payload(coordinator.session)["elevation_gain_m"] is None


async def test_ended_gain_survives_storage_restore(coordinator, hass):
    manager = coordinator.manager
    await manager.async_setup()
    session = coordinator.session
    session.last_point = coordinator._to_point({"altitude": 125, "elevationGainMeters": 42.5})
    session.status = LiveTrackStatus.ENDED
    session.actual_end = datetime.now(UTC)
    manager.sessions.clear()
    manager.ended_sessions["gain"] = session
    await manager.async_save_storage()
    restored = GarminLiveTrackManager(hass, SimpleNamespace(), manager.store, {})
    await restored.async_setup()
    await restored.async_restore_sessions_from_storage()
    assert GarminUserStatusSensor(restored, "runner").extra_state_attributes["elevation_gain_m"] == 42.5
    assert GarminUserTracker(restored, "runner").extra_state_attributes["elevation_gain_m"] == 42.5
    legacy = manager._serialize_point(session.last_point)
    legacy.pop("elevation_gain_m")
    assert manager._deserialize_point(legacy).elevation_gain_m is None
