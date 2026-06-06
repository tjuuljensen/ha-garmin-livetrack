import pytest
from datetime import UTC, datetime, timedelta
import re

from custom_components.garmin_livetrack.client import GarminFetchResult, GarminTrackpointResult
from custom_components.garmin_livetrack.coordinator import GarminLiveTrackManager, LiveTrackSessionCoordinator
from custom_components.garmin_livetrack.binary_sensor import GarminAnyActiveBinarySensor
from custom_components.garmin_livetrack.const import EVENT_POINT_RECEIVED
from custom_components.garmin_livetrack.icons import activity_icon
from custom_components.garmin_livetrack.models import (
    LiveTrackError,
    LiveTrackIdentity,
    LiveTrackPoint,
    LiveTrackSession,
    LiveTrackSource,
    LiveTrackStatus,
    normalize_activity,
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
    def __init__(self):
        self.user_agent = None

    def parse_livetrack_identity(self, **kwargs):
        return LiveTrackIdentity("abc", "def", "https://livetrack.garmin.com/session/abc/token/def", "https://livetrack.garmin.com/session/abc/token/d...f", kwargs.get("source"))
    async def fetch(self, identity): return DummyFetch()


class UrlAwareDummyClient:
    def __init__(self):
        self.user_agent = None

    def parse_livetrack_identity(self, **kwargs):
        url = kwargs.get("url") or ""
        match = re.search(r"/session/([^/?]+)/token/([^/?]+)", url)
        session_id = match.group(1) if match else "abc"
        token = match.group(2) if match else "def"
        return LiveTrackIdentity(
            session_id,
            token,
            f"https://livetrack.garmin.com/session/{session_id}/token/{token}",
            f"https://livetrack.garmin.com/session/{session_id}/token/{token[:1]}...{token[-1:]}",
            kwargs.get("source"),
        )

    async def fetch(self, identity):
        fetch = DummyFetch()
        fetch.session = {
            "sessionId": identity.session_id,
            "userDisplayName": "Runner",
            "activityType": "running",
        }
        return fetch


class SequenceFetch:
    def __init__(self, *, fetched_at, trackpoint_count, session=None, last_trackpoint=None, ok=True, errors=None, page_status=200, api_status=200):
        self.ok = ok
        self.session = session or {}
        self.source = {"trackpoints_source": "api_or_payload"}
        self.trackpoint_count = trackpoint_count
        self.last_trackpoint = last_trackpoint or {}
        self.fetched_at = fetched_at
        self.errors = errors or []
        self.page_status = page_status
        self.api_status = api_status


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("Running", "running"),
        ("RUNNING", "running"),
        ("Trail Running", "running"),
        ("Mountain Biking", "cycling"),
        ("kayaking", "kayak"),
        ("canoe", "kayak"),
        ("rowing", "rowing"),
        ("unknown_activity", "unknown_activity"),
        (None, "other"),
    ],
)
def test_normalize_activity_aliases(raw_value, expected):
    assert normalize_activity(raw_value) == expected


@pytest.mark.parametrize(
    ("activity", "is_active", "expected_icon"),
    [
        ("running", True, "mdi:run-fast"),
        ("running", False, "mdi:run"),
        ("cycling", True, "mdi:bike-fast"),
        ("cycling", False, "mdi:bike"),
        ("kayak", True, "mdi:kayaking"),
        ("kayak", False, "mdi:kayaking"),
        ("rowing", True, "mdi:rowing"),
        ("unknown_activity", True, "mdi:map-marker-path"),
    ],
)
def test_activity_icon_mapping(activity, is_active, expected_icon):
    assert activity_icon(activity, is_active) == expected_icon


def test_point_parser_accepts_garmin_metric_aliases(hass):
    start = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    m = GarminLiveTrackManager(hass, DummyClient(), DummyStore(), {})
    identity = LiveTrackIdentity(
        "metric-aliases",
        "token",
        "https://livetrack.garmin.com/session/metric-aliases/token/token",
        "redacted",
        LiveTrackSource.SERVICE,
    )
    session = LiveTrackSession(identity, "Runner", "walking", start, None, None, start, None, None, None, 0, LiveTrackStatus.ACTIVE)
    coord = LiveTrackSessionCoordinator(m, session)

    point = coord._to_point(
        {
            "timestamp": "2026-01-01T10:05:00Z",
            "fitnessPointData": {
                "latitude": 55.67,
                "longitude": 12.56,
                "altitudeMeters": 25.5,
                "speedMetersPerSecond": 1.2,
                "totalDistanceMeters": 345.6,
                "elapsedDurationSeconds": 300,
                "heartRateInBeatsPerMinute": 123,
                "powerInWatts": 180,
                "cadenceRpm": 82,
            },
        }
    )

    assert point.latitude == 55.67
    assert point.longitude == 12.56
    assert point.altitude_m == 25.5
    assert point.speed_mps == 1.2
    assert point.distance_m == 345.6
    assert point.duration_s == 300
    assert point.heart_rate_bpm == 123
    assert point.power_w == 180
    assert point.cadence == 82


def test_point_sequence_derives_missing_duration_and_distance(hass):
    start = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    m = GarminLiveTrackManager(hass, DummyClient(), DummyStore(), {})
    identity = LiveTrackIdentity(
        "derived-metrics",
        "token",
        "https://livetrack.garmin.com/session/derived-metrics/token/token",
        "redacted",
        LiveTrackSource.SERVICE,
    )
    session = LiveTrackSession(identity, "Runner", "walking", start, None, None, start, None, None, None, 0, LiveTrackStatus.ACTIVE)
    coord = LiveTrackSessionCoordinator(m, session)

    point = coord._to_point_sequence(
        [
            {
                "dateTime": "2026-01-01T10:00:00Z",
                "position": {"lat": 55.67, "lon": 12.56},
            },
            {
                "dateTime": "2026-01-01T10:05:00Z",
                "position": {"lat": 55.671, "lon": 12.561},
            },
        ],
        None,
    )

    assert point.duration_s == 300
    assert point.distance_m == pytest.approx(128.0, abs=5.0)
    assert point.speed_mps == pytest.approx(point.distance_m / 300.0, rel=0.01)


def test_point_sequence_preserves_previous_metrics_when_final_point_is_sparse(hass):
    start = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    m = GarminLiveTrackManager(hass, DummyClient(), DummyStore(), {})
    identity = LiveTrackIdentity(
        "sparse-end",
        "token",
        "https://livetrack.garmin.com/session/sparse-end/token/token",
        "redacted",
        LiveTrackSource.SERVICE,
    )
    session = LiveTrackSession(identity, "Runner", "walking", start, None, None, start, None, None, None, 0, LiveTrackStatus.ACTIVE)
    coord = LiveTrackSessionCoordinator(m, session)

    point = coord._to_point_sequence(
        [
            {
                "dateTime": "2026-01-01T10:15:00Z",
                "position": {"lat": 55.67, "lon": 12.56},
                "fitnessPointData": {
                    "altitudeMeters": 25.91,
                    "heartRateInBeatsPerMinute": 123,
                },
            },
            {
                "dateTime": "2026-01-01T10:16:00Z",
                "position": {"lat": 55.6701, "lon": 12.5601},
                "eventTypes": ["END"],
            },
        ],
        None,
    )

    assert point.altitude_m == 25.91
    assert point.heart_rate_bpm == 123
    assert point.duration_s == 960


class SequenceClient:
    def __init__(self, fetches):
        self._fetches = list(fetches)

    async def fetch(self, identity):
        if not self._fetches:
            raise AssertionError("No more fetches configured")
        return self._fetches.pop(0)


class AdaptiveClient:
    def __init__(self, session_fetches, trackpoint_fetches, legacy_fetches=None):
        self._session_fetches = list(session_fetches)
        self._trackpoint_fetches = list(trackpoint_fetches)
        self._legacy_fetches = list(legacy_fetches or [])
        self.trackpoint_calls = []
        self.user_agent = None

    async def fetch(self, identity):
        raise AssertionError("Adaptive mode should not call fetch() directly")

    async def fetch_session(self, identity):
        if not self._session_fetches:
            raise AssertionError("No more session fetches configured")
        return self._session_fetches.pop(0)

    async def fetch_trackpoints(self, identity, begin=None):
        self.trackpoint_calls.append(begin)
        if not self._trackpoint_fetches:
            raise AssertionError("No more trackpoint fetches configured")
        return self._trackpoint_fetches.pop(0)

    async def fetch_legacy_full(self, identity):
        if not self._legacy_fetches:
            raise AssertionError("No legacy fetch configured")
        return self._legacy_fetches.pop(0)


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
async def test_configured_user_agent_is_applied_to_client(hass):
    m = GarminLiveTrackManager(hass, DummyClient(), DummyStore(), {"user_agent": "CustomUA/2.0"})
    await m.async_setup()
    assert m.client.user_agent == "CustomUA/2.0"


@pytest.mark.asyncio
async def test_update_profile_controls_effective_interval_when_no_override(hass):
    m = GarminLiveTrackManager(
        hass,
        DummyClient(),
        DummyStore(),
        {"update_profile": "balanced"},
    )
    await m.async_setup()
    assert m._effective_update_interval_seconds() == 30

    m.options["update_profile"] = "adaptive"
    assert m._effective_update_interval_seconds() == 15

    m.options["update_profile"] = "extended"
    assert m._effective_update_interval_seconds() == 600

    m.options["update_profile"] = "custom"
    m.options["update_interval_seconds"] = 42
    assert m._effective_update_interval_seconds() == 42
    m.options["use_garmin_trackpoint_frequency"] = True
    assert m._uses_garmin_trackpoint_frequency() is True


@pytest.mark.asyncio
async def test_storage_user_policy_migration_drops_notification_fields(hass):
    store = DummyStore()
    store.data = {
        "known_users": {
            "runner": {
                "name": "Runner",
                "enabled": True,
                "mode": "normal",
                "enable_notifications": True,
                "notify_service": "notify.mobile_app_phone",
                "ios_notification_style": True,
                "allowed_activities": ["running"],
            }
        }
    }
    m = GarminLiveTrackManager(hass, DummyClient(), store, {})
    await m.async_setup()

    assert "runner" in m.known_users
    assert m.known_users["runner"].allowed_activities == ["running"]
    assert "enable_notifications" not in store.data["known_users"]["runner"]
    assert "notify_service" not in store.data["known_users"]["runner"]
    assert "ios_notification_style" not in store.data["known_users"]["runner"]


@pytest.mark.asyncio
async def test_shape_change_signal_syncs_repair_issue(monkeypatch, hass):
    calls = []

    from custom_components.garmin_livetrack import coordinator as coordinator_module

    monkeypatch.setattr(
        coordinator_module,
        "async_sync_shape_change_issue",
        lambda hass_arg, suspected, consecutive_anomaly_count: calls.append(
            {
                "suspected": suspected,
                "count": consecutive_anomaly_count,
            }
        ),
    )

    m = GarminLiveTrackManager(hass, DummyClient(), DummyStore(), {})
    m.shape_change_suspected = True
    m.shape_change_count = 3
    await m._update_shape_change_signal()

    assert calls == [{"suspected": True, "count": 3}]


@pytest.mark.asyncio
async def test_startup_missing_trackpoints_that_later_recovers_does_not_raise_shape_change(hass):
    base = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    fetches = [
        SequenceFetch(
            ok=False,
            fetched_at=base,
            trackpoint_count=0,
            session={"sessionId": "recover-1", "userDisplayName": "Runner", "activityType": "running"},
            last_trackpoint={},
            errors=[LiveTrackError("missing_trackpoints", "No trackpoints", base, True)],
        ),
        SequenceFetch(
            ok=True,
            fetched_at=base + timedelta(minutes=2),
            trackpoint_count=1,
            session={"sessionId": "recover-1", "userDisplayName": "Runner", "activityType": "running"},
            last_trackpoint={
                "dateTime": "2026-01-01T10:02:00Z",
                "position": {"lat": 55.67, "lon": 12.56},
                "distance": 500,
                "duration": 120,
            },
        ),
    ]
    m = GarminLiveTrackManager(hass, SequenceClient(fetches), DummyStore(), {})
    await m.async_setup()
    identity = LiveTrackIdentity("recover-1", "token", "https://livetrack.garmin.com/session/recover-1/token/token", "redacted", LiveTrackSource.SERVICE)
    session = LiveTrackSession(identity, None, None, None, None, None, base, None, None, None, 0, LiveTrackStatus.DISCOVERED)
    coord = LiveTrackSessionCoordinator(m, session)
    m.sessions["recover-1"] = coord

    await coord._refresh_once()
    assert coord.session.status == LiveTrackStatus.WAITING_FOR_TRACKPOINT
    assert m.shape_change_count == 0
    assert m.shape_change_suspected is False

    await coord._refresh_once()
    assert coord.session.status == LiveTrackStatus.ACTIVE
    assert m.shape_change_count == 0
    assert m.shape_change_suspected is False


@pytest.mark.asyncio
async def test_strict_false_registers_unknown_user_and_tracks_immediately(hass):
    m = GarminLiveTrackManager(
        hass,
        UrlAwareDummyClient(),
        DummyStore(),
        {"strict_users": False, "accept_first_seen_users": False},
    )
    await m.async_setup()

    result = await m.async_add_url(
        "https://livetrack.garmin.com/session/session-1/token/token-1",
        LiveTrackSource.SERVICE,
    )

    assert result.ok is True
    assert "runner" in m.known_users
    assert m.known_users["runner"].enabled is True
    assert m.known_users["runner"].mode == "normal"
    assert "session-1" in m.sessions


@pytest.mark.asyncio
async def test_strict_true_rejects_unknown_user_when_accept_first_false(hass):
    m = GarminLiveTrackManager(
        hass,
        UrlAwareDummyClient(),
        DummyStore(),
        {"strict_users": True, "accept_first_seen_users": False},
    )
    await m.async_setup()

    result = await m.async_add_url(
        "https://livetrack.garmin.com/session/session-2/token/token-2",
        LiveTrackSource.SERVICE,
    )

    assert result.ok is False
    assert result.status == LiveTrackStatus.REJECTED_USER
    assert "runner" in m.known_users
    assert m.known_users["runner"].enabled is False
    assert m.known_users["runner"].mode == "register_only"
    assert m.known_users["runner"].first_event_consumed is False
    assert "session-2" not in m.sessions


@pytest.mark.asyncio
async def test_strict_true_accept_first_allows_one_event_then_rejects_later(hass):
    m = GarminLiveTrackManager(
        hass,
        UrlAwareDummyClient(),
        DummyStore(),
        {"strict_users": True, "accept_first_seen_users": True},
    )
    await m.async_setup()

    first = await m.async_add_url(
        "https://livetrack.garmin.com/session/session-3/token/token-3",
        LiveTrackSource.SERVICE,
    )
    assert first.ok is True
    assert "runner" in m.known_users
    assert m.known_users["runner"].enabled is False
    assert m.known_users["runner"].mode == "one_event_only"
    assert m.known_users["runner"].first_event_consumed is True
    assert "session-3" in m.sessions

    second = await m.async_add_url(
        "https://livetrack.garmin.com/session/session-4/token/token-4",
        LiveTrackSource.SERVICE,
    )
    assert second.ok is False
    assert second.status == LiveTrackStatus.REJECTED_USER
    assert "session-4" not in m.sessions


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
        activity_type_raw="Trail Running",
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
            cadence=88,
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
    assert attrs["activity_type_raw"] == "Trail Running"
    assert attrs["source"] == "service"
    assert attrs["distance_km"] == 12.345
    assert attrs["duration_min"] == 60.0
    assert attrs["duration_hms"] == "01:00:00"
    assert attrs["speed_mps"] == 3.5
    assert attrs["speed_kmh"] == 12.6
    assert attrs["pace_min_km"] == 4.76
    assert "pace_min_per_km" not in attrs
    assert attrs["altitude_m"] == 42
    assert attrs["cadence"] == 88
    assert attrs["has_location"] is False
    assert attrs["heart_rate_bpm"] == 150
    assert attrs["activity_icon"] == "mdi:run"
    assert attrs["status_icon"] == "mdi:check-circle-outline"


@pytest.mark.asyncio
async def test_ended_session_summary_persists_across_restore(hass):
    store = DummyStore()
    first = GarminLiveTrackManager(hass, DummyClient(), store, {"retain_ended_hours": 24})
    await first.async_setup()
    base = datetime.now(UTC) - timedelta(hours=1)
    session = LiveTrackSession(
        identity=LiveTrackIdentity(
            "ended-persist",
            "secret-token",
            "https://livetrack.garmin.com/session/ended-persist/token/secret-token",
            "https://livetrack.garmin.com/session/ended-persist/token/sec...ken",
            LiveTrackSource.SERVICE,
        ),
        garmin_user="Runner",
        activity_type="running",
        start=base,
        expected_end=base + timedelta(hours=1),
        actual_end=base + timedelta(minutes=45),
        first_seen=base,
        last_fetch=base + timedelta(minutes=46),
        last_success=base + timedelta(minutes=46),
        last_point=LiveTrackPoint(
            timestamp=base + timedelta(minutes=45),
            latitude=55.67,
            longitude=12.56,
            distance_m=5432,
            duration_s=1800,
            speed_mps=3.0,
            heart_rate_bpm=142,
        ),
        trackpoint_count=42,
        status=LiveTrackStatus.ENDED,
        end_reason="inactive_no_end",
    )
    first.ended_sessions["ended-persist"] = session
    first.ended_session_debug["ended-persist"] = {
        "page_status": 200,
        "api_status": 200,
        "trackpoints_source": "trackpoints_common",
        "post_trackpoint_frequency_s": 15,
        "last_trackpoint_fetch": (base + timedelta(minutes=46)).isoformat(),
        "next_trackpoints_allowed_at": (base + timedelta(minutes=47)).isoformat(),
        "backoff_until": None,
        "consecutive_http_failures": 0,
        "last_http_status": None,
    }
    await first.async_save_storage()

    restored = GarminLiveTrackManager(
        hass,
        DummyClient(),
        store,
        {"retain_ended_hours": 24, "expose_debug_attributes": True},
    )
    await restored.async_setup()
    await restored.async_restore_sessions_from_storage()

    assert restored.sessions == {}
    restored_session = restored.ended_sessions["ended-persist"]
    assert restored_session.identity.token == ""
    assert restored_session.garmin_user == "Runner"
    assert restored_session.end_reason == "inactive_no_end"
    assert restored_session.last_point is not None
    assert restored_session.last_point.distance_m == 5432

    sensor = GarminUserStatusSensor(restored, "runner")
    assert sensor.native_value == "ended"
    attrs = sensor.extra_state_attributes
    assert attrs["distance_km"] == 5.432
    assert attrs["duration_min"] == 30.0
    assert attrs["heart_rate_bpm"] == 142
    assert attrs["page_status"] == 200
    assert attrs["api_status"] == 200
    assert attrs["trackpoints_source"] == "trackpoints_common"
    assert attrs["post_trackpoint_frequency_s"] == 15
    assert attrs["poll_task_alive"] is False


@pytest.mark.asyncio
async def test_user_status_sensor_exposes_debug_attributes_when_enabled(hass):
    m = GarminLiveTrackManager(hass, DummyClient(), DummyStore(), {"expose_debug_attributes": True})
    await m.async_setup()
    await m.async_add_url("https://livetrack.garmin.com/session/abc/token/def", LiveTrackSource.SERVICE)

    entity = GarminUserStatusSensor(m, "runner")
    attrs = entity.extra_state_attributes
    assert attrs["source"] == "service"
    assert attrs["page_status"] == 200
    assert attrs["api_status"] == 200
    assert attrs["trackpoints_source"] == "api_or_payload"
    assert "poll_task_alive" in attrs


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
    assert attrs["active_summaries"][0]["source"] == "service"
    assert attrs["active_summaries"][0]["activity_type"] == "running"
    assert attrs["active_summaries"][0]["activity_type_raw"] == "running"
    assert attrs["active_summaries"][0]["activity_icon"] == "mdi:run-fast"


@pytest.mark.asyncio
async def test_adaptive_mode_skips_trackpoint_fetch_before_next_allowed_time(hass):
    base = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    client = AdaptiveClient(
        session_fetches=[
            GarminFetchResult(
                ok=True,
                session={
                    "sessionId": "adaptive-1",
                    "userDisplayName": "Runner",
                    "activityType": "kayaking",
                    "postTrackPointFrequency": 10,
                },
                fetched_at=base,
                source={"session_source": "api_or_payload"},
            ),
            GarminFetchResult(
                ok=True,
                session={
                    "sessionId": "adaptive-1",
                    "userDisplayName": "Runner",
                    "activityType": "kayaking",
                    "postTrackPointFrequency": 10,
                },
                fetched_at=base + timedelta(seconds=5),
                source={"session_source": "api_or_payload"},
            ),
        ],
        trackpoint_fetches=[
            GarminTrackpointResult(
                ok=True,
                trackpoints=[
                    {
                        "dateTime": "2026-01-01T10:00:00Z",
                        "position": {"lat": 55.67, "lon": 12.56},
                        "speed": 2.5,
                        "distance": 500,
                        "duration": 120,
                        "cadence": 60,
                    }
                ],
                fetched_at=base,
                source={"trackpoints_source": "trackpoints_common"},
            )
        ],
    )
    m = GarminLiveTrackManager(
        hass,
        client,
        DummyStore(),
        {"update_profile": "adaptive"},
    )
    await m.async_setup()
    identity = LiveTrackIdentity("adaptive-1", "token", "https://livetrack.garmin.com/session/adaptive-1/token/token", "redacted", LiveTrackSource.SERVICE)
    session = LiveTrackSession(identity, None, None, None, None, None, base, None, None, None, 0, LiveTrackStatus.DISCOVERED)
    coord = LiveTrackSessionCoordinator(m, session)

    await coord._refresh_once()
    assert client.trackpoint_calls == [None]
    assert coord.post_trackpoint_frequency_s == 10
    assert coord.next_trackpoints_allowed_at == base + timedelta(seconds=12)

    await coord._refresh_once()
    assert client.trackpoint_calls == [None]
    assert coord.session.activity_type == "kayak"
    assert coord.session.activity_type_raw == "kayaking"


@pytest.mark.asyncio
async def test_point_received_event_fires_only_for_new_points(hass):
    base = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    client = AdaptiveClient(
        session_fetches=[
            GarminFetchResult(
                ok=True,
                session={
                    "sessionId": "adaptive-2",
                    "userDisplayName": "Runner",
                    "activityType": "Trail Running",
                    "postTrackPointFrequency": 10,
                },
                fetched_at=base,
                source={"session_source": "api_or_payload"},
            ),
            GarminFetchResult(
                ok=True,
                session={
                    "sessionId": "adaptive-2",
                    "userDisplayName": "Runner",
                    "activityType": "Trail Running",
                    "postTrackPointFrequency": 10,
                },
                fetched_at=base + timedelta(seconds=20),
                source={"session_source": "api_or_payload"},
            ),
        ],
        trackpoint_fetches=[
            GarminTrackpointResult(
                ok=True,
                trackpoints=[
                    {
                        "dateTime": "2026-01-01T10:00:00Z",
                        "position": {"lat": 55.67, "lon": 12.56},
                        "speed": 3.0,
                        "distance": 1000,
                        "duration": 300,
                        "heartRate": 150,
                    }
                ],
                fetched_at=base,
                source={"trackpoints_source": "trackpoints_common"},
            ),
            GarminTrackpointResult(
                ok=True,
                trackpoints=[],
                fetched_at=base + timedelta(seconds=20),
                source={"trackpoints_source": "trackpoints_common"},
            ),
        ],
    )
    m = GarminLiveTrackManager(
        hass,
        client,
        DummyStore(),
        {"update_profile": "adaptive"},
    )
    await m.async_setup()
    identity = LiveTrackIdentity("adaptive-2", "token", "https://livetrack.garmin.com/session/adaptive-2/token/token", "redacted", LiveTrackSource.SERVICE)
    session = LiveTrackSession(identity, None, None, None, None, None, base, None, None, None, 0, LiveTrackStatus.DISCOVERED)
    coord = LiveTrackSessionCoordinator(m, session)

    await coord._refresh_once()
    await coord._refresh_once()

    point_events = [event for event in hass.bus.fired if event["event_type"] == EVENT_POINT_RECEIVED]
    assert len(point_events) == 1
    payload = point_events[0]["event_data"]
    assert payload["activity_type"] == "running"
    assert payload["activity_type_raw"] == "Trail Running"
    assert payload["activity_icon"] == "mdi:run-fast"
    assert payload["source"] == "service"
    assert payload["speed_kmh"] == 10.8
    assert payload["pace_min_km"] == 5.56
    assert payload["distance_km"] == 1.0
    assert payload["duration_hms"] == "00:05:00"
    assert "token" not in payload


@pytest.mark.asyncio
async def test_no_end_no_progress_transitions_to_ending_then_ended(hass):
    base = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    fetches = [
        SequenceFetch(
            fetched_at=base,
            trackpoint_count=1,
            session={"sessionId": "stale-1", "userDisplayName": "Runner", "activityType": "running"},
            last_trackpoint={
                "dateTime": "2026-01-01T10:00:00Z",
                "position": {"lat": 55.67, "lon": 12.56},
                "distance": 1000,
                "duration": 600,
            },
        ),
        SequenceFetch(
            fetched_at=base + timedelta(minutes=20),
            trackpoint_count=1,
            session={"sessionId": "stale-1", "userDisplayName": "Runner", "activityType": "running"},
            last_trackpoint={
                "dateTime": "2026-01-01T10:00:00Z",
                "position": {"lat": 55.67, "lon": 12.56},
                "distance": 1000,
                "duration": 600,
            },
        ),
        SequenceFetch(
            fetched_at=base + timedelta(minutes=36),
            trackpoint_count=1,
            session={"sessionId": "stale-1", "userDisplayName": "Runner", "activityType": "running"},
            last_trackpoint={
                "dateTime": "2026-01-01T10:00:00Z",
                "position": {"lat": 55.67, "lon": 12.56},
                "distance": 1000,
                "duration": 600,
            },
        ),
        SequenceFetch(
            fetched_at=base + timedelta(minutes=41),
            trackpoint_count=1,
            session={"sessionId": "stale-1", "userDisplayName": "Runner", "activityType": "running"},
            last_trackpoint={
                "dateTime": "2026-01-01T10:00:00Z",
                "position": {"lat": 55.67, "lon": 12.56},
                "distance": 1000,
                "duration": 600,
            },
        ),
    ]
    m = GarminLiveTrackManager(
        hass,
        SequenceClient(fetches),
        DummyStore(),
        {"stale_minutes": 15, "finalization_minutes": 20, "retain_ended_hours": 100000},
    )
    await m.async_setup()
    identity = LiveTrackIdentity("stale-1", "token", "https://livetrack.garmin.com/session/stale-1/token/token", "redacted", LiveTrackSource.SERVICE)
    session = LiveTrackSession(identity, None, None, None, None, None, base, None, None, None, 0, LiveTrackStatus.DISCOVERED)
    coord = LiveTrackSessionCoordinator(m, session)
    m.sessions["stale-1"] = coord

    await coord._refresh_once()
    assert coord.session.status == LiveTrackStatus.ACTIVE
    await coord._refresh_once()
    assert coord.session.status == LiveTrackStatus.ACTIVE
    await coord._refresh_once()
    assert coord.session.status == LiveTrackStatus.ENDING
    assert coord.end_reason == "inactive_no_end"
    await coord._refresh_once()
    assert coord.session.status == LiveTrackStatus.ENDED
    assert coord.session.end_reason == "inactive_no_end"
    assert m.ended_sessions["stale-1"].end_reason == "inactive_no_end"
    assert "stale-1" in m.ended_sessions


@pytest.mark.asyncio
async def test_no_trackpoints_still_transitions_to_stale(hass):
    base = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    fetches = [
        SequenceFetch(
            fetched_at=base,
            trackpoint_count=0,
            session={"sessionId": "empty-1", "userDisplayName": "Runner", "activityType": "running"},
            last_trackpoint={},
        ),
        SequenceFetch(
            fetched_at=base + timedelta(minutes=26),
            trackpoint_count=0,
            session={"sessionId": "empty-1", "userDisplayName": "Runner", "activityType": "running"},
            last_trackpoint={},
        ),
    ]
    m = GarminLiveTrackManager(
        hass,
        SequenceClient(fetches),
        DummyStore(),
        {"stale_minutes": 15, "initial_trackpoint_wait_minutes": 10},
    )
    await m.async_setup()
    identity = LiveTrackIdentity("empty-1", "token", "https://livetrack.garmin.com/session/empty-1/token/token", "redacted", LiveTrackSource.SERVICE)
    session = LiveTrackSession(identity, None, None, None, None, None, base, None, None, None, 0, LiveTrackStatus.DISCOVERED)
    coord = LiveTrackSessionCoordinator(m, session)
    m.sessions["empty-1"] = coord

    await coord._refresh_once()
    assert coord.session.status == LiveTrackStatus.WAITING_FOR_TRACKPOINT
    await coord._refresh_once()
    assert coord.session.status == LiveTrackStatus.STALE
    assert coord.end_reason == "no_trackpoints"
    assert "empty-1" in m.ended_sessions
    assert m.shape_change_count == 1
    assert m.shape_change_suspected is False


@pytest.mark.asyncio
async def test_inferred_ending_beats_stale_when_session_end_is_past(hass):
    base = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    end_time = base - timedelta(minutes=1)
    fetches = [
        SequenceFetch(
            fetched_at=base,
            trackpoint_count=1,
            session={
                "sessionId": "ended-1",
                "userDisplayName": "Runner",
                "activityType": "running",
                "end": end_time.isoformat(),
            },
            last_trackpoint={
                "dateTime": "2026-01-01T10:00:00Z",
                "position": {"lat": 55.67, "lon": 12.56},
                "distance": 1000,
                "duration": 600,
            },
        ),
        SequenceFetch(
            fetched_at=base + timedelta(minutes=6),
            trackpoint_count=1,
            session={
                "sessionId": "ended-1",
                "userDisplayName": "Runner",
                "activityType": "running",
                "end": end_time.isoformat(),
            },
            last_trackpoint={
                "dateTime": "2026-01-01T10:00:00Z",
                "position": {"lat": 55.67, "lon": 12.56},
                "distance": 1000,
                "duration": 600,
            },
        ),
    ]
    m = GarminLiveTrackManager(
        hass,
        SequenceClient(fetches),
        DummyStore(),
        {"stale_minutes": 2, "finalization_minutes": 1, "retain_ended_hours": 100000},
    )
    await m.async_setup()
    identity = LiveTrackIdentity("ended-1", "token", "https://livetrack.garmin.com/session/ended-1/token/token", "redacted", LiveTrackSource.SERVICE)
    session = LiveTrackSession(identity, None, None, None, None, None, base, None, None, None, 0, LiveTrackStatus.DISCOVERED)
    coord = LiveTrackSessionCoordinator(m, session)
    m.sessions["ended-1"] = coord

    await coord._refresh_once()
    assert coord.session.status == LiveTrackStatus.ENDED
    assert coord.end_reason == "session_end"
    assert coord.session.end_reason == "session_end"
    assert "ended-1" in m.ended_sessions


@pytest.mark.asyncio
async def test_cleanup_legacy_entities_removes_deprecated_session_count(monkeypatch, hass):
    class FakeEntry:
        def __init__(self, entity_id, unique_id, platform="garmin_livetrack", disabled_by=None):
            self.entity_id = entity_id
            self.unique_id = unique_id
            self.platform = platform
            self.disabled_by = disabled_by

    class FakeRegistry:
        def __init__(self):
            self.entities = {
                "keep_active_count": FakeEntry(
                    "sensor.garmin_livetrack_active_count",
                    "garmin_livetrack_active_count",
                ),
                "remove_session_count": FakeEntry(
                    "sensor.garmin_livetrack_session_count",
                    "garmin_livetrack_session_count",
                ),
            }
            self.removed = []

        def async_remove(self, entity_id):
            self.removed.append(entity_id)

    fake_registry = FakeRegistry()

    from custom_components.garmin_livetrack import coordinator as coordinator_module

    monkeypatch.setattr(coordinator_module.er, "async_get", lambda _hass: fake_registry)
    m = GarminLiveTrackManager(hass, DummyClient(), DummyStore(), {})
    await m.async_setup()
    count = await m.async_cleanup_legacy_entities()

    assert count == 1
    assert fake_registry.removed == ["sensor.garmin_livetrack_session_count"]


@pytest.mark.asyncio
async def test_rate_limit_backoff_escalates_and_clears_on_success(hass):
    base = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    fetches = [
        SequenceFetch(
            fetched_at=base,
            trackpoint_count=0,
            ok=False,
            errors=[LiveTrackError("session_http_error", "HTTP 429", base, True)],
            api_status=429,
        ),
        SequenceFetch(
            fetched_at=base + timedelta(minutes=2),
            trackpoint_count=0,
            ok=False,
            errors=[LiveTrackError("session_http_error", "HTTP 429", base + timedelta(minutes=2), True)],
            api_status=429,
        ),
        SequenceFetch(
            fetched_at=base + timedelta(minutes=6),
            trackpoint_count=1,
            session={"sessionId": "backoff-429", "userDisplayName": "Runner", "activityType": "running"},
            last_trackpoint={
                "dateTime": "2026-01-01T10:06:00Z",
                "position": {"lat": 55.67, "lon": 12.56},
            },
        ),
    ]
    m = GarminLiveTrackManager(hass, SequenceClient(fetches), DummyStore(), {})
    await m.async_setup()
    identity = LiveTrackIdentity("backoff-429", "token", "https://livetrack.garmin.com/session/backoff-429/token/token", "redacted", LiveTrackSource.SERVICE)
    session = LiveTrackSession(identity, None, None, None, None, None, base, None, None, None, 0, LiveTrackStatus.DISCOVERED)
    coord = LiveTrackSessionCoordinator(m, session)

    await coord._refresh_once()
    assert coord.consecutive_http_failures == 1
    assert coord.last_http_status == 429
    assert coord.backoff_until == base + timedelta(minutes=2)

    coord.backoff_until = base
    await coord._refresh_once()
    assert coord.consecutive_http_failures == 2
    assert coord.backoff_until == base + timedelta(minutes=6)

    coord.backoff_until = base + timedelta(minutes=2)
    await coord._refresh_once()
    assert coord.consecutive_http_failures == 0
    assert coord.backoff_until is None
    assert coord.last_http_status is None
    assert coord.session.status == LiveTrackStatus.ACTIVE


@pytest.mark.asyncio
async def test_server_error_backoff_and_debug_attributes(hass):
    base = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    fetches = [
        SequenceFetch(
            fetched_at=base,
            trackpoint_count=0,
            ok=False,
            errors=[LiveTrackError("session_http_error", "HTTP 503", base, True)],
            api_status=503,
        ),
        SequenceFetch(
            fetched_at=base + timedelta(seconds=31),
            trackpoint_count=1,
            session={"sessionId": "backoff-503", "userDisplayName": "Runner", "activityType": "running"},
            last_trackpoint={
                "dateTime": "2026-01-01T10:00:31Z",
                "position": {"lat": 55.67, "lon": 12.56},
            },
        ),
    ]
    m = GarminLiveTrackManager(hass, SequenceClient(fetches), DummyStore(), {"expose_debug_attributes": True})
    await m.async_setup()
    identity = LiveTrackIdentity("backoff-503", "token", "https://livetrack.garmin.com/session/backoff-503/token/token", "redacted", LiveTrackSource.SERVICE)
    session = LiveTrackSession(identity, "Runner", "running", None, None, None, base, None, None, None, 0, LiveTrackStatus.DISCOVERED)
    coord = LiveTrackSessionCoordinator(m, session)
    m.sessions["backoff-503"] = coord

    await coord._refresh_once()
    assert coord.consecutive_http_failures == 1
    assert coord.last_http_status == 503
    assert coord.backoff_until == base + timedelta(seconds=30)

    sensor = GarminUserStatusSensor(m, "runner")
    attrs = sensor.extra_state_attributes
    assert attrs["consecutive_http_failures"] == 1
    assert attrs["last_http_status"] == 503
    assert attrs["backoff_until"] == (base + timedelta(seconds=30)).isoformat()

    coord.backoff_until = base
    await coord._refresh_once()
    assert coord.consecutive_http_failures == 0
    assert coord.backoff_until is None


@pytest.mark.asyncio
async def test_strict_true_allows_user_from_allowed_users_registry(hass):
    m = GarminLiveTrackManager(
        hass,
        UrlAwareDummyClient(),
        DummyStore(),
        {
            "strict_users": True,
            "accept_first_seen_users": False,
            "allowed_users": ["Runner"],
        },
    )
    await m.async_setup()

    assert "runner" in m.known_users
    assert m.known_users["runner"].enabled is True
    assert m.known_users["runner"].mode == "normal"

    result = await m.async_add_url(
        "https://livetrack.garmin.com/session/session-allowed/token/token-allowed",
        LiveTrackSource.SERVICE,
    )

    assert result.ok is True
    assert result.status in {LiveTrackStatus.ACTIVE, LiveTrackStatus.WAITING_FOR_TRACKPOINT}
    assert "session-allowed" in m.sessions


@pytest.mark.asyncio
async def test_async_remove_user_purges_user_state(hass):
    m = GarminLiveTrackManager(
        hass,
        UrlAwareDummyClient(),
        DummyStore(),
        {
            "allowed_users": ["Runner", "Other"],
            "user_policies": {
                "Runner": {
                    "name": "Runner",
                    "enabled": True,
                    "mode": "normal",
                },
                "Other": {
                    "name": "Other",
                    "enabled": True,
                    "mode": "normal",
                },
            },
        },
    )
    await m.async_setup()

    runner_identity = LiveTrackIdentity(
        "runner-live",
        "token",
        "https://livetrack.garmin.com/session/runner-live/token/token",
        "redacted",
        LiveTrackSource.SERVICE,
    )
    runner_session = LiveTrackSession(
        runner_identity,
        "Runner",
        "walking",
        None,
        None,
        None,
        datetime.now(UTC),
        None,
        None,
        None,
        0,
        LiveTrackStatus.ACTIVE,
    )
    runner_coord = LiveTrackSessionCoordinator(m, runner_session)
    m.sessions[runner_identity.session_id] = runner_coord

    ended_identity = LiveTrackIdentity(
        "runner-ended",
        "token",
        "https://livetrack.garmin.com/session/runner-ended/token/token",
        "redacted",
        LiveTrackSource.SERVICE,
    )
    ended_session = LiveTrackSession(
        ended_identity,
        "Runner",
        "walking",
        None,
        None,
        datetime.now(UTC),
        datetime.now(UTC),
        None,
        None,
        None,
        0,
        LiveTrackStatus.ENDED,
    )
    m.ended_sessions[ended_identity.session_id] = ended_session

    changed = await m.async_remove_user("Runner")

    assert changed is True
    assert "runner" not in m.known_users
    assert m.options["allowed_users"] == ["Other"]
    assert list(m.options["user_policies"].keys()) == ["Other"]
    assert runner_identity.session_id not in m.sessions
    assert ended_identity.session_id not in m.ended_sessions
