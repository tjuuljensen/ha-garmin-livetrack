import pytest
from datetime import UTC, datetime, timedelta
import re

from custom_components.garmin_livetrack.coordinator import GarminLiveTrackManager, LiveTrackSessionCoordinator
from custom_components.garmin_livetrack.binary_sensor import GarminAnyActiveBinarySensor
from custom_components.garmin_livetrack.const import (
    DEFAULT_NOTIFICATION_END_TEMPLATE,
    DEFAULT_NOTIFICATION_START_TEMPLATE,
)
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
    def __init__(self, *, fetched_at, trackpoint_count, session=None, last_trackpoint=None, ok=True):
        self.ok = ok
        self.session = session or {}
        self.source = {"trackpoints_source": "api_or_payload"}
        self.trackpoint_count = trackpoint_count
        self.last_trackpoint = last_trackpoint or {}
        self.fetched_at = fetched_at
        self.errors = []
        self.page_status = 200
        self.api_status = 200


class SequenceClient:
    def __init__(self, fetches):
        self._fetches = list(fetches)

    async def fetch(self, identity):
        if not self._fetches:
            raise AssertionError("No more fetches configured")
        return self._fetches.pop(0)


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
    assert attrs["source"] == "service"
    assert attrs["distance_km"] == 12.345
    assert attrs["duration_min"] == 60.0
    assert attrs["heart_rate_bpm"] == 150
    assert attrs["activity_icon"] == "mdi:run"
    assert attrs["status_icon"] == "mdi:check-circle-outline"
    assert "page_status" not in attrs
    assert "api_status" not in attrs
    assert "trackpoints_source" not in attrs
    assert "poll_task_alive" not in attrs


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


@pytest.mark.asyncio
async def test_notification_start_template_formats_message(hass):
    m = GarminLiveTrackManager(
        hass,
        DummyClient(),
        DummyStore(),
        {
            "enable_notifications": True,
            "notify_service": "notify.notify",
            "notification_start_template": "START {user} {activity} via {source} {session_id_hash}",
        },
    )
    await m.async_setup()
    await m.async_add_url("https://livetrack.garmin.com/session/abc/token/def", LiveTrackSource.SERVICE)

    assert hass.services.calls
    call = hass.services.calls[-1]
    assert call["domain"] == "notify"
    assert call["service"] == "notify"
    assert call["payload"]["message"].startswith("START Runner running via service ")


@pytest.mark.asyncio
async def test_notification_end_template_formats_message(hass):
    m = GarminLiveTrackManager(
        hass,
        DummyClient(),
        DummyStore(),
        {
            "enable_notifications": True,
            "notify_service": "notify.notify",
            "notification_end_template": "END {user} {activity} {reason} {distance_km} {duration_min}",
        },
    )
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
        ),
        trackpoint_count=123,
        status=LiveTrackStatus.ACTIVE,
    )

    await m.async_notify_end(session, "inactive_no_end")
    call = hass.services.calls[-1]
    assert call["payload"]["message"] == "END Runner running inactive without Garmin END 12.35 60.0"


@pytest.mark.asyncio
async def test_invalid_notification_template_falls_back_to_default(hass):
    m = GarminLiveTrackManager(
        hass,
        DummyClient(),
        DummyStore(),
        {
            "enable_notifications": True,
            "notify_service": "notify.notify",
            "notification_start_template": "START {missing}",
            "notification_end_template": "END {missing}",
        },
    )
    await m.async_setup()
    await m.async_add_url("https://livetrack.garmin.com/session/abc/token/def", LiveTrackSource.SERVICE)
    start_call = hass.services.calls[-1]
    assert start_call["payload"]["message"] == DEFAULT_NOTIFICATION_START_TEMPLATE.format(
        user="Runner",
        activity="running",
    )

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
        start=None,
        expected_end=None,
        actual_end=None,
        first_seen=datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
        last_fetch=None,
        last_success=None,
        last_point=None,
        trackpoint_count=0,
        status=LiveTrackStatus.ACTIVE,
    )
    await m.async_notify_end(session, "session_end")
    end_call = hass.services.calls[-1]
    assert end_call["payload"]["message"] == DEFAULT_NOTIFICATION_END_TEMPLATE.format(
        user="Runner",
        activity="running",
        reason="Garmin session end",
    )


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
        {"stale_minutes": 15, "finalization_minutes": 20},
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
        {"stale_minutes": 2, "finalization_minutes": 1},
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
