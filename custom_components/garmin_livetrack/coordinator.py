from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable

from homeassistant.core import Event, SupportsResponse, callback
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_ACCEPT_FIRST_SEEN_USERS,
    CONF_ACTIVITY_FILTER,
    CONF_ALLOWED_USERS,
    CONF_FINALIZATION_MINUTES,
    CONF_INITIAL_TRACKPOINT_WAIT,
    CONF_LISTEN_TO_IMAP_EVENTS,
    CONF_MAX_RUNTIME_HOURS,
    CONF_STALE_MINUTES,
    CONF_STRICT_USERS,
    CONF_UPDATE_PROFILE,
    CONF_UPDATE_INTERVAL,
    CONF_USE_GARMIN_TRACKPOINT_FREQUENCY,
    CONF_USER_AGENT,
    CONF_USER_POLICIES,
    DEFAULT_USER_AGENT,
    EVENT_IMAP_CONTENT,
    EVENT_POINT_RECEIVED,
    EVENT_SESSION_ADDED,
    EVENT_SESSION_ENDED,
    EVENT_SESSION_REJECTED,
    EVENT_SESSION_UPDATED,
    SERVICE_ADD_URL,
    SERVICE_CLEAR_ENDED,
    SERVICE_CLEANUP_LEGACY_ENTITIES,
    SERVICE_LIST_USERS,
    SERVICE_REMOVE_USER,
    SERVICE_RELOAD_USERS,
    SERVICE_REFRESH_ALL,
    SERVICE_REFRESH_SESSION,
    SERVICE_SET_USER_POLICY,
    SERVICE_STOP_SESSION,
)
from .icons import activity_icon
from .models import (
    distance_km_from_m,
    duration_hms_from_seconds,
    LiveTrackIdentity,
    LiveTrackPoint,
    LiveTrackSession,
    LiveTrackSource,
    LiveTrackStatus,
    has_location,
    pace_min_km_from_speed_mps,
    extract_event_types,
    normalize_activity,
    parse_garmin_datetime,
    speed_kmh_from_mps,
    stable_session_hash,
)
from .repairs import async_sync_shape_change_issue
from .const import (
    UPDATE_PROFILE_DEFAULT_INITIAL_WAIT_MINUTES,
    UPDATE_PROFILE_DEFAULT_INTERVALS,
    UPDATE_PROFILE_DEFAULT_STALE_MINUTES,
    UPDATE_PROFILE_DEFAULT_USE_GARMIN_TRACKPOINT_FREQUENCY,
)

URL_RE = re.compile(r"https://livetrack\.garmin\.com/session/[^\"'>\s]+", re.IGNORECASE)
_LOGGER = logging.getLogger(__name__)
LEGACY_NOTIFICATION_POLICY_KEYS = {"enable_notifications", "notify_service", "ios_notification_style"}

ACTIVE_STATES = {
    LiveTrackStatus.FETCHING,
    LiveTrackStatus.WAITING_FOR_TRACKPOINT,
    LiveTrackStatus.ACTIVE,
    LiveTrackStatus.ENDING,
}


@dataclass
class AddUrlResult:
    ok: bool
    status: LiveTrackStatus
    session_id_hash: str | None = None
    message: str = ""


@dataclass
class UserPolicy:
    name: str
    enabled: bool = True
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    first_event_consumed: bool = False
    mode: str = "normal"
    allowed_activities: list[str] | None = None


class LiveTrackSessionCoordinator:
    def __init__(self, manager: GarminLiveTrackManager, session: LiveTrackSession) -> None:
        self.manager = manager
        self.session = session
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.end_reason: str | None = None
        self._no_progress_since: datetime | None = None
        self._last_progress_count: int = 0
        self._last_progress_point_ts: datetime | None = None
        self.last_page_status: int | None = None
        self.last_api_status: int | None = None
        self.last_source_branch: str = "none"
        self._logged_first_success = False
        self.next_trackpoints_allowed_at: datetime | None = None
        self.post_trackpoint_frequency_s: int | None = None
        self.last_trackpoint_fetch: datetime | None = None
        self.backoff_until: datetime | None = None
        self.consecutive_http_failures: int = 0
        self.last_http_status: int | None = None

    async def async_start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self.manager.startup_debug[f"poller_start_{stable_session_hash(self.session.identity.session_id)}"] = datetime.now(UTC).isoformat()
        _LOGGER.debug(
            "Garmin LiveTrack startup diag: starting poller for session=%s source=%s user=%s",
            stable_session_hash(self.session.identity.session_id),
            self.session.identity.source.value,
            self.session.garmin_user or "unknown",
        )
        self._task = self.manager.hass.async_create_task(self._run_loop())

    async def async_stop(self, reason: str = "manual") -> None:
        self.end_reason = reason
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def async_first_fetch(self) -> None:
        await self._refresh_once()

    async def _run_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self._refresh_once()
            except Exception as err:  # noqa: BLE001
                self.manager.last_error = f"poll_error:{type(err).__name__}"
                self.session.status = LiveTrackStatus.GARMIN_ERROR
                self.manager._notify_listeners()
                break
            if self.session.status in {
                LiveTrackStatus.ENDED,
                LiveTrackStatus.EXPIRED,
                LiveTrackStatus.STALE,
                LiveTrackStatus.STOPPED,
                LiveTrackStatus.GARMIN_ERROR,
            }:
                break
            interval = self.manager._effective_update_interval_seconds()
            if self.session.status == LiveTrackStatus.WAITING_FOR_TRACKPOINT:
                initial_wait_minutes = self.manager._effective_initial_trackpoint_wait_minutes()
                within_initial = (datetime.now(UTC) - self.session.first_seen) < timedelta(minutes=initial_wait_minutes)
                if within_initial:
                    interval = min(interval, 10)
            timeout = self.manager._loop_wait_seconds(interval, self.next_trackpoints_allowed_at)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                pass

    async def _refresh_once(self) -> None:
        now = datetime.now(UTC)
        if self.backoff_until and now < self.backoff_until:
            await self._handle_backoff_window(now)
            return
        self.session.status = LiveTrackStatus.FETCHING
        previous_last_point = self.session.last_point
        previous_last_timestamp = previous_last_point.timestamp if previous_last_point else None
        fetch = await self._fetch_runtime_state()
        self.session.last_fetch = fetch.fetched_at
        self.last_page_status = fetch.page_status
        self.last_api_status = fetch.api_status
        self.last_source_branch = str(fetch.source.get("trackpoints_source", "none")) if isinstance(fetch.source, dict) else "none"
        self.session.errors.extend(fetch.errors[-3:])
        self._apply_fetch_backoff(fetch)
        if fetch.errors:
            self.manager.last_error = fetch.errors[-1].code
            codes = {e.code for e in fetch.errors}
            shape_signal_changed = False
            if "missing_session" in codes or "missing_trackpoints" in codes:
                self.manager.shape_change_count += 1
                if self.manager.shape_change_count >= 3:
                    if not self.manager.shape_change_suspected:
                        self.manager.shape_change_suspected = True
                        shape_signal_changed = True
            else:
                if self.manager.shape_change_count or self.manager.shape_change_suspected:
                    self.manager.shape_change_count = 0
                    if self.manager.shape_change_suspected:
                        self.manager.shape_change_suspected = False
                    shape_signal_changed = True
            if shape_signal_changed:
                await self.manager._update_shape_change_signal()
        elif self.manager.shape_change_count:
            self.manager.shape_change_count = max(0, self.manager.shape_change_count - 1)
            if self.manager.shape_change_count == 0 and self.manager.shape_change_suspected:
                self.manager.shape_change_suspected = False
                await self.manager._update_shape_change_signal()

        if fetch.ok:
            first_success = self.session.last_success is None
            self.session.last_success = fetch.fetched_at
            self.session.garmin_user = (fetch.session.get("userDisplayName") or "").strip() or self.session.garmin_user
            activity_raw = (
                fetch.session.get("activityType")
                or fetch.session.get("activity")
                or fetch.session.get("sportType")
                or fetch.session.get("activityName")
                or fetch.last_trackpoint.get("activityType")
                or fetch.last_trackpoint.get("activity")
                or (fetch.last_trackpoint.get("fitnessPointData") or {}).get("activityType")
                or (fetch.last_trackpoint.get("fitnessPointData") or {}).get("activity")
                or self.session.activity_type_raw
            )
            if activity_raw:
                self.session.activity_type_raw = str(activity_raw)
                self.session.activity_type = normalize_activity(activity_raw)
            self.session.start = parse_garmin_datetime(fetch.session.get("start")) or self.session.start
            self.session.expected_end = parse_garmin_datetime(fetch.session.get("end")) or self.session.expected_end
            self._update_trackpoint_frequency(fetch.session)
            self.session.trackpoint_count = fetch.trackpoint_count
            self.session.last_point = self._to_point(fetch.last_trackpoint) or self.session.last_point

            if self.session.trackpoint_count > 0:
                self.session.status = LiveTrackStatus.ACTIVE
            else:
                self.session.status = LiveTrackStatus.WAITING_FOR_TRACKPOINT

            if self.session.last_point and self.post_trackpoint_frequency_s:
                self.next_trackpoints_allowed_at = self._compute_next_trackpoints_allowed_at(self.session.last_point.timestamp)
            elif self.manager._uses_garmin_trackpoint_frequency():
                self.next_trackpoints_allowed_at = fetch.fetched_at + timedelta(seconds=max(2, self.manager._effective_update_interval_seconds()))

            if first_success and not self._logged_first_success:
                self._logged_first_success = True
                self.manager.startup_debug[f"first_success_{stable_session_hash(self.session.identity.session_id)}"] = fetch.fetched_at.isoformat()
                _LOGGER.debug(
                    "Garmin LiveTrack startup diag: first fetch success for session=%s status=%s trackpoints=%s source=%s user=%s",
                    stable_session_hash(self.session.identity.session_id),
                    self.session.status.value,
                    self.session.trackpoint_count,
                    self.last_source_branch,
                    self.session.garmin_user or "unknown",
                )

            current_timestamp = self.session.last_point.timestamp if self.session.last_point else None
            if current_timestamp and current_timestamp != previous_last_timestamp:
                self.manager._fire_point_received(self.session)

            if await self._handle_no_progress(now=fetch.fetched_at):
                return
            await self._handle_end_state(first_success=first_success)
        else:
            stale_cutoff = timedelta(minutes=self.manager._effective_stale_minutes())
            if self.session.last_success and (datetime.now(UTC) - self.session.last_success) > stale_cutoff:
                self.session.status = LiveTrackStatus.STALE
                self.end_reason = "fetch_stale"
                await self.manager.async_finalize_session(self, self.end_reason)
                return
            else:
                self.session.status = LiveTrackStatus.WAITING_FOR_TRACKPOINT

        self.manager._notify_listeners()
        self.manager.hass.bus.async_fire(
            EVENT_SESSION_UPDATED,
            self.manager._session_event_payload(self.session),
        )

    async def _fetch_runtime_state(self):
        if not self.manager._uses_garmin_trackpoint_frequency():
            return await self.manager.client.fetch(self.session.identity)
        return await self._fetch_adaptive_state()

    async def _fetch_adaptive_state(self):
        session_fetch = await self.manager.client.fetch_session(self.session.identity)
        if not session_fetch.ok:
            return session_fetch

        fetched_at = session_fetch.fetched_at
        current_last_raw = self._point_to_fetch_payload(self.session.last_point)
        current_count = self.session.trackpoint_count
        should_fetch_trackpoints = (
            self.session.last_point is None
            or self.next_trackpoints_allowed_at is None
            or fetched_at >= self.next_trackpoints_allowed_at
        )
        if not should_fetch_trackpoints:
            session_fetch.trackpoint_count = current_count
            session_fetch.last_trackpoint = current_last_raw
            session_fetch.source["trackpoints_source"] = "deferred"
            return session_fetch

        begin = self.session.last_point.timestamp if self.session.last_point else None
        trackpoint_fetch = await self.manager.client.fetch_trackpoints(self.session.identity, begin=begin)
        self.last_trackpoint_fetch = trackpoint_fetch.fetched_at

        incremental_points = self._new_trackpoints(trackpoint_fetch.trackpoints, self.session.last_point)
        if incremental_points:
            session_fetch.trackpoint_count = current_count + len(incremental_points)
            session_fetch.last_trackpoint = incremental_points[-1]
            session_fetch.source["trackpoints_source"] = "trackpoints_common"
            session_fetch.errors.extend(trackpoint_fetch.errors)
            return session_fetch

        if not trackpoint_fetch.errors and begin is not None:
            session_fetch.trackpoint_count = current_count
            session_fetch.last_trackpoint = current_last_raw
            session_fetch.source["trackpoints_source"] = "trackpoints_common"
            return session_fetch

        legacy = await self.manager.client.fetch_legacy_full(self.session.identity)
        if self.session.last_point:
            fallback_points = self._new_trackpoints(legacy.trackpoints, self.session.last_point)
            if fallback_points:
                legacy.trackpoint_count = current_count + len(fallback_points)
                legacy.last_trackpoint = fallback_points[-1]
            else:
                legacy.trackpoint_count = current_count
                legacy.last_trackpoint = current_last_raw
        legacy.errors = session_fetch.errors + trackpoint_fetch.errors + legacy.errors
        if isinstance(legacy.source, dict):
            legacy.source["session_source"] = legacy.source.get("session_source", "api_or_payload")
        return legacy

    def _new_trackpoints(self, points: list[dict], previous_point: LiveTrackPoint | None) -> list[dict]:
        if not points:
            return []
        previous_ts = previous_point.timestamp if previous_point else None
        if previous_ts is None:
            return [point for point in points if isinstance(point, dict)]
        new_points: list[dict] = []
        for point in points:
            point_ts = parse_garmin_datetime(point.get("dateTime")) if isinstance(point, dict) else None
            if point_ts and point_ts > previous_ts:
                new_points.append(point)
        return new_points

    def _update_trackpoint_frequency(self, session_payload: dict) -> None:
        raw = None
        if isinstance(session_payload, dict):
            raw = session_payload.get("postTrackPointFrequency")
            if raw is None:
                raw = session_payload.get("post_track_point_frequency")
        try:
            value = int(raw) if raw is not None else None
        except (TypeError, ValueError):
            value = None
        self.post_trackpoint_frequency_s = value if value and value > 0 else None

    def _compute_next_trackpoints_allowed_at(self, timestamp: datetime | None) -> datetime | None:
        if timestamp is None:
            return None
        if self.post_trackpoint_frequency_s:
            return timestamp + timedelta(seconds=self.post_trackpoint_frequency_s + 2)
        interval = self.manager._effective_update_interval_seconds()
        return timestamp + timedelta(seconds=interval)

    def _point_to_fetch_payload(self, point: LiveTrackPoint | None) -> dict:
        if point is None:
            return {}
        payload: dict[str, object] = {
            "dateTime": point.timestamp.isoformat().replace("+00:00", "Z") if point.timestamp else None,
            "position": {},
            "fitnessPointData": {},
        }
        if point.latitude is not None:
            payload["position"]["lat"] = point.latitude
            payload["fitnessPointData"]["latitude"] = point.latitude
        if point.longitude is not None:
            payload["position"]["lon"] = point.longitude
            payload["fitnessPointData"]["longitude"] = point.longitude
        for key, value in {
            "altitude": point.altitude_m,
            "speed": point.speed_mps,
            "distance": point.distance_m,
            "duration": point.duration_s,
            "heartRate": point.heart_rate_bpm,
            "power": point.power_w,
            "cadence": point.cadence,
        }.items():
            if value is not None:
                payload[key] = value
        if point.event_types:
            payload["eventTypes"] = list(point.event_types)
        return payload


    async def _handle_backoff_window(self, now: datetime) -> None:
        if self.session.status not in {LiveTrackStatus.ENDING, LiveTrackStatus.ENDED, LiveTrackStatus.STALE, LiveTrackStatus.EXPIRED, LiveTrackStatus.STOPPED}:
            if self.session.trackpoint_count > 0:
                self.session.status = LiveTrackStatus.ACTIVE
            else:
                self.session.status = LiveTrackStatus.WAITING_FOR_TRACKPOINT
        if await self._handle_no_progress(now=now):
            return
        await self._handle_end_state()
        self.manager._notify_listeners()
        self.manager.hass.bus.async_fire(
            EVENT_SESSION_UPDATED,
            self.manager._session_event_payload(self.session),
        )

    def _apply_fetch_backoff(self, fetch) -> None:
        decision = self._classify_fetch_backoff(fetch)
        if decision is None:
            self._clear_fetch_backoff()
            return
        base_seconds, max_seconds, http_status = decision
        delay = min(max_seconds, base_seconds * (2 ** max(0, self.consecutive_http_failures)))
        self.consecutive_http_failures += 1
        self.backoff_until = fetch.fetched_at + timedelta(seconds=delay)
        self.last_http_status = http_status

    def _clear_fetch_backoff(self) -> None:
        self.backoff_until = None
        self.consecutive_http_failures = 0
        self.last_http_status = None

    def _classify_fetch_backoff(self, fetch) -> tuple[int, int, int | None] | None:
        statuses = [status for status in (fetch.api_status, fetch.page_status) if isinstance(status, int)]
        if 429 in statuses:
            return (120, 900, 429)
        status_5xx = next((status for status in statuses if status >= 500), None)
        if status_5xx is not None:
            return (30, 600, status_5xx)
        if 403 in statuses:
            return (60, 300, 403)
        retryable_codes = {error.code for error in getattr(fetch, "errors", []) if getattr(error, "retryable", False)}
        if retryable_codes & {"page_request_error", "session_request_error", "trackpoints_request_error", "malformed_response"}:
            return (30, 600, None)
        return None

    async def _handle_end_state(self, first_success: bool = False) -> None:
        now = datetime.now(UTC)
        max_runtime_hours = int(self.manager.options.get(CONF_MAX_RUNTIME_HOURS, 23))
        if self.session.start and self._safe_elapsed(now, self.session.start) > timedelta(hours=max_runtime_hours):
            self.session.status = LiveTrackStatus.EXPIRED
            self.end_reason = "max_runtime"
            await self.manager.async_finalize_session(self, self.end_reason)
            return

        event_types = set(self.session.last_point.event_types if self.session.last_point else [])
        ended_by_event = "END" in event_types
        ended_by_time = self._safe_is_past(self.session.expected_end, now)
        finalization = timedelta(minutes=int(self.manager.options.get(CONF_FINALIZATION_MINUTES, 10)))
        # If Garmin explicitly emits END, finalize quickly instead of holding
        # full finalization window meant for inferred endings.
        if ended_by_event:
            self.session.status = LiveTrackStatus.ENDED
            self.session.actual_end = self._best_end_timestamp(now)
            self.end_reason = "end_event"
            await self.manager.async_finalize_session(self, self.end_reason)
            return

        # Historical/manual loads should not sit in "ending" when Garmin's
        # session end is already well in the past before we ever started
        # tracking it in this runtime.
        ended_long_ago = bool(
            self.session.expected_end
            and self._safe_elapsed(now, self.session.expected_end) >= finalization
        )
        if ended_by_time and (first_success or ended_long_ago):
            self.session.status = LiveTrackStatus.ENDED
            self.session.actual_end = self._best_end_timestamp(now)
            self.end_reason = "session_end"
            await self.manager.async_finalize_session(self, self.end_reason)
            return

        if ended_by_event or ended_by_time:
            if self.session.status != LiveTrackStatus.ENDING:
                self.session.status = LiveTrackStatus.ENDING
                self.end_reason = "end_event" if ended_by_event else "session_end"
                if not hasattr(self, "_ending_since"):
                    self._ending_since = now
            if now - self._ending_since >= finalization:
                self.session.status = LiveTrackStatus.ENDED
                self.session.actual_end = self._best_end_timestamp(now)
                await self.manager.async_finalize_session(self, self.end_reason or "ended")

    def _best_end_timestamp(self, fallback: datetime) -> datetime:
        if self.session.last_point and self.session.last_point.timestamp:
            return self.session.last_point.timestamp
        if self.session.expected_end:
            return self.session.expected_end
        return fallback

    async def _handle_no_progress(self, now: datetime) -> bool:
        stale_cutoff = timedelta(minutes=self.manager._effective_stale_minutes())
        initial_wait = timedelta(minutes=self.manager._effective_initial_trackpoint_wait_minutes())
        finalization = timedelta(minutes=int(self.manager.options.get(CONF_FINALIZATION_MINUTES, 10)))
        current_count = self.session.trackpoint_count
        current_ts = self.session.last_point.timestamp if self.session.last_point else None
        ending_inferred = self._safe_is_past(self.session.expected_end, now)

        progressed = False
        if current_count > self._last_progress_count:
            progressed = True
        elif current_ts and self._last_progress_point_ts and current_ts > self._last_progress_point_ts:
            progressed = True
        elif current_ts and self._last_progress_point_ts is None:
            progressed = True

        if progressed:
            self._last_progress_count = current_count
            self._last_progress_point_ts = current_ts
            self._no_progress_since = None
            return False

        # No points at all after initial wait + stale window => stale/finalize.
        if current_count == 0:
            age = now - self.session.first_seen
            if ending_inferred:
                return False
            if age > (initial_wait + stale_cutoff):
                self.session.status = LiveTrackStatus.STALE
                self.end_reason = "no_trackpoints"
                await self.manager.async_finalize_session(self, self.end_reason)
                return True
            return False

        if self._no_progress_since is None:
            self._no_progress_since = now
            return False

        if ending_inferred:
            return False

        if now - self._no_progress_since > stale_cutoff:
            if self.session.status != LiveTrackStatus.ENDING:
                self.session.status = LiveTrackStatus.ENDING
                self.end_reason = "inactive_no_end"
                self._ending_since = self._no_progress_since
                if now - self._ending_since < finalization:
                    return False
            if not hasattr(self, "_ending_since"):
                self._ending_since = self._no_progress_since
            if now - self._ending_since >= finalization:
                self.session.status = LiveTrackStatus.ENDED
                self.session.actual_end = self._best_end_timestamp(now)
                await self.manager.async_finalize_session(self, self.end_reason or "inactive_no_end")
                return True
        return False

    def _safe_is_past(self, value: datetime | None, now: datetime) -> bool:
        if value is None:
            return False
        try:
            return value < now
        except TypeError:
            fixed = value.replace(tzinfo=UTC) if value.tzinfo is None else value
            return fixed < now

    def _safe_elapsed(self, now: datetime, start: datetime) -> timedelta:
        try:
            return now - start
        except TypeError:
            fixed = start.replace(tzinfo=UTC) if start.tzinfo is None else start
            return now - fixed

    def _to_point(self, raw: dict) -> LiveTrackPoint | None:
        if not raw:
            return None
        pos = raw.get("position") or {}
        fpd = raw.get("fitnessPointData") or {}
        lat = pos.get("lat") if isinstance(pos, dict) else None
        lon = pos.get("lon") if isinstance(pos, dict) else None
        if lat is None:
            lat = fpd.get("latitude")
        if lon is None:
            lon = fpd.get("longitude")
        speed = raw.get("speed") if raw.get("speed") is not None else fpd.get("speed")
        return LiveTrackPoint(
            timestamp=parse_garmin_datetime(raw.get("dateTime")),
            latitude=float(lat) if lat is not None else None,
            longitude=float(lon) if lon is not None else None,
            altitude_m=raw.get("altitude") if raw.get("altitude") is not None else fpd.get("altitude"),
            speed_mps=speed,
            distance_m=raw.get("distance") if raw.get("distance") is not None else fpd.get("distance"),
            duration_s=raw.get("duration") if raw.get("duration") is not None else fpd.get("duration"),
            heart_rate_bpm=raw.get("heartRate") if raw.get("heartRate") is not None else fpd.get("heartRate"),
            power_w=raw.get("power") if raw.get("power") is not None else fpd.get("power"),
            cadence=raw.get("cadence") if raw.get("cadence") is not None else fpd.get("cadence"),
            event_types=extract_event_types(raw),
            raw={},
        )


class GarminLiveTrackManager:
    def __init__(self, hass, client, store, options):
        self.hass = hass
        self.client = client
        self.store = store
        self.options = options
        self.sessions: dict[str, LiveTrackSessionCoordinator] = {}
        self.ended_sessions: dict[str, LiveTrackSession] = {}
        self.known_users: dict[str, UserPolicy] = {}
        self.unsub_imap_listener = None
        self.lock = asyncio.Lock()
        self.last_error = None
        self.shape_change_suspected = False
        self.shape_change_count = 0
        self._listeners: list[Callable[[], None]] = []
        self.startup_debug: dict[str, str | int | bool] = {}
        self._sync_client_options()

    @staticmethod
    def _session_key(session_id: str) -> str:
        return str(session_id).strip().lower()

    @staticmethod
    def _user_key(user: str | None) -> str:
        return str(user or "").strip().lower()

    def _notify_listeners(self) -> None:
        for listener in list(self._listeners):
            listener()

    async def _update_shape_change_signal(self) -> None:
        async_sync_shape_change_issue(
            self.hass,
            suspected=self.shape_change_suspected,
            consecutive_anomaly_count=self.shape_change_count,
        )
        self._notify_listeners()

    def _effective_user_agent(self) -> str:
        value = str(self.options.get(CONF_USER_AGENT, DEFAULT_USER_AGENT) or DEFAULT_USER_AGENT).strip()
        return value or DEFAULT_USER_AGENT

    def _effective_update_profile(self) -> str:
        profile = str(self.options.get(CONF_UPDATE_PROFILE, "conservative") or "conservative").strip().lower()
        if profile not in UPDATE_PROFILE_DEFAULT_INTERVALS:
            return "conservative"
        return profile

    def _uses_garmin_trackpoint_frequency(self) -> bool:
        profile = self._effective_update_profile()
        if profile == "custom":
            return bool(self.options.get(CONF_USE_GARMIN_TRACKPOINT_FREQUENCY, False))
        return bool(UPDATE_PROFILE_DEFAULT_USE_GARMIN_TRACKPOINT_FREQUENCY[profile])

    def _effective_update_interval_seconds(self) -> int:
        profile = self._effective_update_profile()
        if profile == "custom":
            try:
                configured = int(self.options.get(CONF_UPDATE_INTERVAL, int(UPDATE_PROFILE_DEFAULT_INTERVALS["custom"])) or 0)
            except (TypeError, ValueError):
                configured = 0
            if configured > 0:
                return configured
        return int(UPDATE_PROFILE_DEFAULT_INTERVALS[profile])

    def _effective_initial_trackpoint_wait_minutes(self) -> int:
        profile = self._effective_update_profile()
        if profile == "custom":
            try:
                configured = int(self.options.get(CONF_INITIAL_TRACKPOINT_WAIT, UPDATE_PROFILE_DEFAULT_INITIAL_WAIT_MINUTES["custom"]) or 0)
            except (TypeError, ValueError):
                configured = 0
            if configured > 0:
                return configured
        return int(UPDATE_PROFILE_DEFAULT_INITIAL_WAIT_MINUTES[profile])

    def _effective_stale_minutes(self) -> int:
        profile = self._effective_update_profile()
        if profile == "custom":
            try:
                configured = int(self.options.get(CONF_STALE_MINUTES, UPDATE_PROFILE_DEFAULT_STALE_MINUTES["custom"]) or 0)
            except (TypeError, ValueError):
                configured = 0
            if configured > 0:
                return configured
        return int(UPDATE_PROFILE_DEFAULT_STALE_MINUTES[profile])

    def _loop_wait_seconds(self, base_interval: int, next_trackpoints_allowed_at: datetime | None) -> int:
        if not self._uses_garmin_trackpoint_frequency():
            return max(30, base_interval)
        wait_seconds = max(5, min(base_interval, 15))
        if next_trackpoints_allowed_at is None:
            return wait_seconds
        delta = int((next_trackpoints_allowed_at - datetime.now(UTC)).total_seconds())
        if delta <= 0:
            return 2
        return max(2, min(wait_seconds, delta))

    def _sync_client_options(self) -> None:
        if hasattr(self.client, "user_agent"):
            self.client.user_agent = self._effective_user_agent()

    def _session_event_payload(self, session: LiveTrackSession) -> dict:
        point = session.last_point
        speed_kmh = speed_kmh_from_mps(point.speed_mps) if point else None
        pace_min_km = pace_min_km_from_speed_mps(point.speed_mps) if point else None
        duration_hms = duration_hms_from_seconds(point.duration_s) if point else None
        return {
            "session_id_hash": stable_session_hash(session.identity.session_id),
            "user": session.garmin_user,
            "activity_type": session.activity_type,
            "activity_type_raw": session.activity_type_raw,
            "activity_icon": activity_icon(session.activity_type, session.status in ACTIVE_STATES),
            "source": session.identity.source.value,
            "status": session.status.value,
            "latitude": point.latitude if point else None,
            "longitude": point.longitude if point else None,
            "altitude_m": point.altitude_m if point else None,
            "speed_mps": point.speed_mps if point else None,
            "speed_kmh": speed_kmh,
            "pace_min_km": pace_min_km,
            "distance_km": distance_km_from_m(point.distance_m) if point else None,
            "duration_s": point.duration_s if point else None,
            "duration_hms": duration_hms,
            "heart_rate_bpm": point.heart_rate_bpm if point else None,
            "power_w": point.power_w if point else None,
            "cadence": point.cadence if point else None,
            "event_types": list(point.event_types) if point else [],
            "has_location": has_location(point),
        }

    def _fire_point_received(self, session: LiveTrackSession) -> None:
        point = session.last_point
        if point is None:
            return
        self.hass.bus.async_fire(
            EVENT_POINT_RECEIVED,
            self._session_event_payload(session),
        )

    @staticmethod
    def _serialize_point(point: LiveTrackPoint | None) -> dict | None:
        if point is None:
            return None
        return {
            "timestamp": point.timestamp.isoformat() if point.timestamp else None,
            "latitude": point.latitude,
            "longitude": point.longitude,
            "altitude_m": point.altitude_m,
            "speed_mps": point.speed_mps,
            "distance_m": point.distance_m,
            "duration_s": point.duration_s,
            "heart_rate_bpm": point.heart_rate_bpm,
            "power_w": point.power_w,
            "cadence": point.cadence,
            "event_types": point.event_types,
        }

    @staticmethod
    def _deserialize_point(value: dict | None) -> LiveTrackPoint | None:
        if not isinstance(value, dict):
            return None
        return LiveTrackPoint(
            timestamp=parse_garmin_datetime(value.get("timestamp")),
            latitude=value.get("latitude"),
            longitude=value.get("longitude"),
            altitude_m=value.get("altitude_m"),
            speed_mps=value.get("speed_mps"),
            distance_m=value.get("distance_m"),
            duration_s=value.get("duration_s"),
            heart_rate_bpm=value.get("heart_rate_bpm"),
            power_w=value.get("power_w"),
            cadence=value.get("cadence"),
            event_types=list(value.get("event_types") or []),
            raw={},
        )

    def _serialize_session(self, session: LiveTrackSession, *, include_token: bool) -> dict:
        coord = self.sessions.get(self._session_key(session.identity.session_id))
        row = {
            "session_id": self._session_key(session.identity.session_id),
            "redacted_url": session.identity.redacted_url,
            "source": session.identity.source.value,
            "first_seen": session.first_seen.isoformat(),
            "garmin_user": session.garmin_user,
            "activity_type": session.activity_type,
            "activity_type_raw": session.activity_type_raw,
            "start": session.start.isoformat() if session.start else None,
            "expected_end": session.expected_end.isoformat() if session.expected_end else None,
            "actual_end": session.actual_end.isoformat() if session.actual_end else None,
            "last_fetch": session.last_fetch.isoformat() if session.last_fetch else None,
            "last_success": session.last_success.isoformat() if session.last_success else None,
            "last_point": self._serialize_point(session.last_point),
            "trackpoint_count": session.trackpoint_count,
            "status": self._persistable_status(session.status).value,
            "rejected_reason": session.rejected_reason,
            "end_reason": session.end_reason,
            "post_trackpoint_frequency_s": coord.post_trackpoint_frequency_s if coord else None,
            "last_trackpoint_fetch": coord.last_trackpoint_fetch.isoformat() if coord and coord.last_trackpoint_fetch else None,
            "next_trackpoints_allowed_at": coord.next_trackpoints_allowed_at.isoformat() if coord and coord.next_trackpoints_allowed_at else None,
        }
        if include_token:
            row["token"] = session.identity.token
        else:
            row["canonical_url"] = session.identity.canonical_url
        return row

    def _restore_ended_session(self, row: dict) -> LiveTrackSession | None:
        sid = self._session_key(row.get("session_id", ""))
        if not sid:
            return None
        source_value = row.get("source", LiveTrackSource.RECOVERY.value)
        try:
            source = LiveTrackSource(source_value)
        except ValueError:
            source = LiveTrackSource.RECOVERY
        canonical_url = row.get("canonical_url") or ""
        identity = LiveTrackIdentity(
            session_id=sid,
            token="",
            canonical_url=canonical_url,
            redacted_url=row.get("redacted_url", ""),
            source=source,
        )
        status_value = row.get("status", LiveTrackStatus.ENDED.value)
        try:
            status = LiveTrackStatus(status_value)
        except ValueError:
            status = LiveTrackStatus.ENDED
        if status in ACTIVE_STATES:
            status = LiveTrackStatus.ENDED
        return LiveTrackSession(
            identity=identity,
            garmin_user=row.get("garmin_user"),
            activity_type=row.get("activity_type"),
            activity_type_raw=row.get("activity_type_raw"),
            start=parse_garmin_datetime(row.get("start")),
            expected_end=parse_garmin_datetime(row.get("expected_end")),
            actual_end=parse_garmin_datetime(row.get("actual_end")),
            first_seen=parse_garmin_datetime(row.get("first_seen")) or datetime.now(UTC),
            last_fetch=parse_garmin_datetime(row.get("last_fetch")),
            last_success=parse_garmin_datetime(row.get("last_success")),
            last_point=self._deserialize_point(row.get("last_point")),
            trackpoint_count=int(row.get("trackpoint_count") or 0),
            status=status,
            rejected_reason=row.get("rejected_reason"),
            end_reason=row.get("end_reason"),
        )

    def _prune_expired_ended_sessions(self, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        retain = timedelta(hours=int(self.options.get("retain_ended_hours", 24)))
        expired: list[str] = []
        for sid, session in self.ended_sessions.items():
            reference = session.actual_end or session.last_success or session.last_fetch or session.first_seen
            if reference and now - reference > retain:
                expired.append(sid)
        for sid in expired:
            self.ended_sessions.pop(sid, None)
        return len(expired)

    @staticmethod
    def _storage_payload(value: dict | None) -> dict:
        if not isinstance(value, dict):
            return {}
        # Be resilient to wrapped storage shapes seen across environments.
        if "active_sessions" in value or "known_users" in value:
            return value
        data = value.get("data")
        if isinstance(data, dict):
            return data
        return {}

    @staticmethod
    def _persistable_status(status: LiveTrackStatus) -> LiveTrackStatus:
        if status == LiveTrackStatus.FETCHING:
            return LiveTrackStatus.WAITING_FOR_TRACKPOINT
        return status

    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)

        def _unsub() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _unsub

    async def async_setup(self):
        self._sync_client_options()
        await self.async_load_storage()
        self._apply_option_user_policies()
        self._apply_allowed_users_registry()
        self._register_services()
        await self._update_imap_listener()
        await self._update_shape_change_signal()

    async def async_unload(self):
        if self.unsub_imap_listener:
            self.unsub_imap_listener()
            self.unsub_imap_listener = None
        for coord in list(self.sessions.values()):
            await coord.async_stop("unload")
        await self.async_save_storage()

    async def async_add_url(self, url: str, source: LiveTrackSource) -> AddUrlResult:
        async with self.lock:
            try:
                identity = self.client.parse_livetrack_identity(url=url, source=source)
            except ValueError as err:
                return AddUrlResult(False, LiveTrackStatus.INVALID_URL, message=str(err))
            sid = self._session_key(identity.session_id)
            identity.session_id = sid
            if sid in self.sessions:
                return AddUrlResult(True, LiveTrackStatus.DUPLICATE, stable_session_hash(sid), "duplicate")
            if sid in self.ended_sessions:
                return AddUrlResult(True, LiveTrackStatus.DUPLICATE, stable_session_hash(sid), "duplicate_ended")

            session = LiveTrackSession(identity, None, None, None, None, None, datetime.now(UTC), None, None, None, 0, LiveTrackStatus.DISCOVERED)
            coord = LiveTrackSessionCoordinator(self, session)
            await coord.async_first_fetch()
            if not await self.async_validate_session_policy(session):
                payload = self._session_event_payload(session)
                payload["reason"] = session.rejected_reason
                self.hass.bus.async_fire(EVENT_SESSION_REJECTED, payload)
                return AddUrlResult(False, session.status, stable_session_hash(sid), session.rejected_reason or "rejected")

            self._prune_duplicate_waiting_sessions_for_user(session)
            self.sessions[sid] = coord
            await coord.async_start()
            self.hass.bus.async_fire(EVENT_SESSION_ADDED, self._session_event_payload(session))
            await self.async_save_storage()
            self._notify_listeners()
            return AddUrlResult(True, session.status, stable_session_hash(sid), "added")

    async def async_finalize_session(self, coord: LiveTrackSessionCoordinator, reason: str) -> None:
        sid = self._session_key(coord.session.identity.session_id)
        self.sessions.pop(sid, None)
        coord.session.end_reason = reason
        self.ended_sessions[sid] = coord.session
        coord.session.actual_end = coord.session.actual_end or datetime.now(UTC)
        payload = self._session_event_payload(coord.session)
        payload["reason"] = reason
        self.hass.bus.async_fire(EVENT_SESSION_ENDED, payload)
        await self.async_save_storage()
        self._notify_listeners()

    async def async_stop_session(self, session_id: str, reason: str = "manual"):
        sid = self._session_key(session_id)
        coord = self.sessions.pop(sid, None)
        if not coord:
            return
        await coord.async_stop(reason)
        coord.session.status = LiveTrackStatus.STOPPED
        coord.session.end_reason = reason
        coord.session.actual_end = datetime.now(UTC)
        self.ended_sessions[sid] = coord.session
        payload = self._session_event_payload(coord.session)
        payload["reason"] = reason
        self.hass.bus.async_fire(EVENT_SESSION_UPDATED, payload)
        await self.async_save_storage()
        self._notify_listeners()

    async def async_clear_ended(self, older_than_hours=None):
        count = len(self.ended_sessions)
        self.ended_sessions.clear()
        await self.async_save_storage()
        self._notify_listeners()
        return count

    async def async_recover_sessions(self):
        raw = await self.store.async_load() or {}
        data = self._storage_payload(raw)
        async with self.lock:
            seen: set[str] = set()
            for row in data.get("active_sessions", []):
                sid = self._session_key(row["session_id"])
                if sid in self.sessions or sid in seen:
                    continue
                seen.add(sid)
                first_seen = parse_garmin_datetime(row.get("first_seen")) or datetime.now(UTC)
                identity = LiveTrackIdentity(
                    session_id=sid,
                    token=row["token"],
                    canonical_url=f"https://livetrack.garmin.com/session/{sid}/token/{row['token']}",
                    redacted_url=row.get("redacted_url", ""),
                    source=LiveTrackSource(row.get("source", LiveTrackSource.RECOVERY.value)),
                )
                session = LiveTrackSession(identity, row.get("garmin_user"), row.get("activity_type"), parse_garmin_datetime(row.get("start")), parse_garmin_datetime(row.get("expected_end")), None, first_seen, None, None, None, 0, LiveTrackStatus(row.get("status", LiveTrackStatus.DISCOVERED.value)), activity_type_raw=row.get("activity_type_raw"))
                session.status = self._persistable_status(session.status)
                coord = LiveTrackSessionCoordinator(self, session)
                coord.post_trackpoint_frequency_s = row.get("post_trackpoint_frequency_s")
                coord.last_trackpoint_fetch = parse_garmin_datetime(row.get("last_trackpoint_fetch"))
                coord.next_trackpoints_allowed_at = parse_garmin_datetime(row.get("next_trackpoints_allowed_at"))
                self._prune_duplicate_waiting_sessions_for_user(session)
                self.sessions[identity.session_id] = coord
                await coord.async_start()
        self._notify_listeners()

    async def async_restore_sessions_from_storage(self) -> int:
        """Restore recoverable sessions without network fetch, then let pollers run."""
        raw = await self.store.async_load() or {}
        data = self._storage_payload(raw)
        restored = 0
        async with self.lock:
            seen: set[str] = set()
            for row in data.get("active_sessions", []):
                sid = self._session_key(row.get("session_id", ""))
                if not sid or sid in self.sessions or sid in seen:
                    continue
                seen.add(sid)
                source_value = row.get("source", LiveTrackSource.RECOVERY.value)
                try:
                    source = LiveTrackSource(source_value)
                except ValueError:
                    source = LiveTrackSource.RECOVERY
                identity = LiveTrackIdentity(
                    session_id=sid,
                    token=row.get("token", ""),
                    canonical_url=f"https://livetrack.garmin.com/session/{sid}/token/{row.get('token','')}",
                    redacted_url=row.get("redacted_url", ""),
                    source=source,
                )
                status_value = row.get("status", LiveTrackStatus.DISCOVERED.value)
                try:
                    status = LiveTrackStatus(status_value)
                except ValueError:
                    status = LiveTrackStatus.DISCOVERED
                status = self._persistable_status(status)
                session = LiveTrackSession(
                    identity=identity,
                    garmin_user=row.get("garmin_user"),
                    activity_type=row.get("activity_type"),
                    activity_type_raw=row.get("activity_type_raw"),
                    start=parse_garmin_datetime(row.get("start")),
                    expected_end=parse_garmin_datetime(row.get("expected_end")),
                    actual_end=None,
                    first_seen=parse_garmin_datetime(row.get("first_seen")) or datetime.now(UTC),
                    last_fetch=None,
                    last_success=None,
                    last_point=None,
                    trackpoint_count=0,
                    status=status,
                )
                self._prune_duplicate_waiting_sessions_for_user(session)
                coord = LiveTrackSessionCoordinator(self, session)
                coord.post_trackpoint_frequency_s = row.get("post_trackpoint_frequency_s")
                coord.last_trackpoint_fetch = parse_garmin_datetime(row.get("last_trackpoint_fetch"))
                coord.next_trackpoints_allowed_at = parse_garmin_datetime(row.get("next_trackpoints_allowed_at"))
                self.sessions[sid] = coord
                self.startup_debug[f"restored_session_{stable_session_hash(sid)}"] = session.identity.source.value
                _LOGGER.debug(
                    "Garmin LiveTrack startup diag: restored session=%s source=%s user=%s status=%s",
                    stable_session_hash(sid),
                    session.identity.source.value,
                    session.garmin_user or "unknown",
                    session.status.value,
                )
                restored += 1
            for row in data.get("ended_sessions", []):
                session = self._restore_ended_session(row)
                if session is None:
                    continue
                self.ended_sessions[session.identity.session_id] = session
            self._prune_expired_ended_sessions()
        if restored:
            self._notify_listeners()
        return restored

    async def async_start_restored_pollers(self) -> int:
        started = 0
        for coord in list(self.sessions.values()):
            if coord._task and not coord._task.done():
                continue
            await coord.async_start()
            started += 1
        self.startup_debug["restored_pollers_started_total"] = started
        return started

    async def async_save_storage(self):
        active_sessions = []
        seen: set[str] = set()
        for c in self.sessions.values():
            sid = self._session_key(c.session.identity.session_id)
            if sid in seen:
                continue
            seen.add(sid)
            active_sessions.append(self._serialize_session(c.session, include_token=True))

        self._prune_expired_ended_sessions()
        ended_sessions = [
            self._serialize_session(session, include_token=False)
            for session in self.ended_sessions.values()
        ]

        await self.store.async_save(
            {
                "active_sessions": active_sessions,
                "ended_sessions": ended_sessions,
                "known_users": {
                    name: {
                        "name": p.name,
                        "enabled": p.enabled,
                        "first_seen": p.first_seen.isoformat() if p.first_seen else None,
                        "last_seen": p.last_seen.isoformat() if p.last_seen else None,
                        "first_event_consumed": p.first_event_consumed,
                        "mode": p.mode,
                        "allowed_activities": p.allowed_activities,
                    }
                    for name, p in self.known_users.items()
                },
            }
        )

    async def async_load_storage(self):
        raw = await self.store.async_load() or {}
        data = self._storage_payload(raw)
        known_users_payload = data.get("known_users", {}) if isinstance(data.get("known_users"), dict) else {}
        storage_changed = False
        sanitized_known_users: dict[str, dict] = {}
        for raw_name, value in known_users_payload.items():
            if isinstance(value, dict):
                clean_value = dict(value)
                for legacy_key in LEGACY_NOTIFICATION_POLICY_KEYS:
                    if legacy_key in clean_value:
                        clean_value.pop(legacy_key, None)
                        storage_changed = True
                sanitized_known_users[raw_name] = clean_value
            else:
                sanitized_known_users[raw_name] = value
        if storage_changed:
            data["known_users"] = sanitized_known_users
            await self.store.async_save(data)
            _LOGGER.debug("Garmin LiveTrack migrated legacy notification fields out of stored user policies")
        known_users: dict[str, UserPolicy] = {}
        for raw_name, value in sanitized_known_users.items():
            display_name = str(value.get("name", raw_name)).strip()
            key = self._user_key(display_name or raw_name)
            if not key:
                continue
            known_users[key] = UserPolicy(
                name=display_name or str(raw_name),
                enabled=value.get("enabled", True),
                first_seen=parse_garmin_datetime(value.get("first_seen")),
                last_seen=parse_garmin_datetime(value.get("last_seen")),
                first_event_consumed=value.get("first_event_consumed", False),
                mode=value.get("mode", "normal"),
                allowed_activities=self._normalize_allowed_activities(value.get("allowed_activities")),
            )
        self.known_users = known_users

    async def async_reload_users(self):
        await self.async_load_storage()
        self._apply_option_user_policies()
        self._apply_allowed_users_registry()

    async def async_refresh_session(self, session_id: str | None = None, session_id_hash: str | None = None) -> bool:
        target_sid = None
        if session_id:
            sid = self._session_key(session_id)
            if sid in self.sessions:
                target_sid = sid
        elif session_id_hash:
            for candidate in self.sessions:
                if stable_session_hash(candidate) == session_id_hash:
                    target_sid = candidate
                    break
        if not target_sid:
            return False
        await self.sessions[target_sid]._refresh_once()
        return True

    async def async_refresh_all(self) -> int:
        count = 0
        for coord in list(self.sessions.values()):
            await coord._refresh_once()
            count += 1
        return count

    async def async_cleanup_legacy_entities(self) -> int:
        registry = er.async_get(self.hass)
        to_remove: list[str] = []
        expected_user_keys: set[str] = set()
        for name in self.known_users:
            key = self._user_key(name)
            if key:
                expected_user_keys.add(key)
        for coord in self.sessions.values():
            key = self._user_key(coord.session.garmin_user)
            if key:
                expected_user_keys.add(key)

        expected_user_uids: set[str] = set()
        for key in expected_user_keys:
            h = stable_session_hash(key)
            expected_user_uids.add(f"garmin_livetrack_user_status_{h}")
            expected_user_uids.add(f"garmin_livetrack_user_active_{h}")
            expected_user_uids.add(f"garmin_livetrack_user_tracker_{h}")

        for entry in list(registry.entities.values()):
            if entry.platform != "garmin_livetrack":
                continue
            if entry.disabled_by is not None:
                continue
            uid = entry.unique_id or ""
            # Keep current per-user and aggregate entity families.
            if uid.startswith("garmin_livetrack_user_"):
                if uid in expected_user_uids:
                    continue
                to_remove.append(entry.entity_id)
                continue
            if uid in {
                "garmin_livetrack_active_count",
                "garmin_livetrack_last_error",
                "garmin_livetrack_any_active",
            }:
                continue
            # Remove only unavailable/orphan candidates.
            if entry.entity_id in self.hass.states.async_entity_ids():
                continue
            to_remove.append(entry.entity_id)

        for entity_id in to_remove:
            registry.async_remove(entity_id)
        return len(to_remove)

    async def async_set_user_policy(
        self,
        user: str,
        enabled: bool | None = None,
        mode: str | None = None,
        allowed_activities=None,
    ) -> bool:
        name = (user or "").strip()
        if not name:
            return False
        key = self._user_key(name)
        policy = self.known_users.get(key)
        now = datetime.now(UTC)
        if policy is None:
            policy = UserPolicy(name=name, enabled=True, first_seen=now, last_seen=now, first_event_consumed=False, mode="normal")
            self.known_users[key] = policy
        else:
            policy.name = name

        if enabled is not None:
            policy.enabled = bool(enabled)
        if mode in {"normal", "register_only", "one_event_only"}:
            policy.mode = mode
        normalized_allowed = self._normalize_allowed_activities(allowed_activities)
        if allowed_activities is not None:
            policy.allowed_activities = normalized_allowed
        policy.last_seen = now
        self._sync_allowed_user(policy.name)
        await self.async_save_storage()
        self._notify_listeners()
        return True

    async def async_remove_user(self, user: str) -> bool:
        name = (user or "").strip()
        if not name:
            return False
        key = self._user_key(name)
        removed = self.known_users.pop(key, None)
        current = [u for u in self.options.get(CONF_ALLOWED_USERS, []) if isinstance(u, str)]
        self.options[CONF_ALLOWED_USERS] = [u for u in current if self._user_key(u) != key]
        await self.async_save_storage()
        self._notify_listeners()
        return removed is not None

    async def async_list_users(self) -> list[dict]:
        rows: list[dict] = []
        for name, policy in sorted(self.known_users.items(), key=lambda item: item[0].lower()):
            rows.append(
                {
                    "name": policy.name,
                    "enabled": policy.enabled,
                    "mode": policy.mode,
                    "first_event_consumed": policy.first_event_consumed,
                    "first_seen": policy.first_seen.isoformat() if policy.first_seen else None,
                    "last_seen": policy.last_seen.isoformat() if policy.last_seen else None,
                    "allowed_activities": policy.allowed_activities,
                    "activity_policy_mode": self._activity_policy_mode(policy.name),
                    "effective_activity_filter": self._effective_activity_filter(policy.name),
                }
            )
        return rows

    async def async_handle_imap_event(self, event: Event):
        text = " ".join(str(event.data.get(k, "")) for k in ("custom", "text", "body", "subject"))
        text = text.replace("=\r\n", "").replace("=\n", "")
        m = URL_RE.search(text)
        if m:
            await self.async_add_url(m.group(0), LiveTrackSource.IMAP)

    async def async_validate_session_policy(self, session: LiveTrackSession):
        strict = self.options.get(CONF_STRICT_USERS, False)
        user = (session.garmin_user or "").strip()
        user_key = self._user_key(user)
        accept_first = self.options.get(CONF_ACCEPT_FIRST_SEEN_USERS, False)
        now = datetime.now(UTC)

        if user:
            policy = self.known_users.get(user_key)
            if policy is None:
                if strict and not accept_first:
                    # Register-only mode: user must be explicitly enabled later.
                    self.known_users[user_key] = UserPolicy(
                        name=user,
                        enabled=False,
                        first_seen=now,
                        last_seen=now,
                        first_event_consumed=False,
                        mode="register_only",
                    )
                    self._sync_allowed_user(user)
                    session.status = LiveTrackStatus.REJECTED_USER
                    session.rejected_reason = "rejected_user"
                    return False
                if strict and accept_first:
                    # One event only: first unknown event is accepted, then disabled.
                    self.known_users[user_key] = UserPolicy(
                        name=user,
                        enabled=False,
                        first_seen=now,
                        last_seen=now,
                        first_event_consumed=True,
                        mode="one_event_only",
                    )
                    self._sync_allowed_user(user)
                else:
                    # strict=false: register and track immediately.
                    self.known_users[user_key] = UserPolicy(
                        name=user,
                        enabled=True,
                        first_seen=now,
                        last_seen=now,
                        first_event_consumed=False,
                        mode="normal",
                    )
                    self._sync_allowed_user(user)
            else:
                policy.name = user
                policy.last_seen = now
                if strict and not policy.enabled:
                    session.status = LiveTrackStatus.REJECTED_USER
                    session.rejected_reason = "rejected_user"
                    return False
        elif strict:
            session.status = LiveTrackStatus.REJECTED_USER
            session.rejected_reason = "rejected_user"
            return False

        if not self._effective_activity_allowed(session.garmin_user, session.activity_type):
            session.status = LiveTrackStatus.REJECTED_ACTIVITY
            session.rejected_reason = "rejected_activity"
            return False
        return True

    def _sync_allowed_user(self, user: str) -> None:
        current = [u for u in self.options.get(CONF_ALLOWED_USERS, []) if isinstance(u, str)]
        target_key = self._user_key(user)
        if any(self._user_key(existing) == target_key for existing in current):
            return
        self.options[CONF_ALLOWED_USERS] = [*current, user]

    def _apply_option_user_policies(self) -> None:
        payload = self.options.get(CONF_USER_POLICIES, {})
        if not isinstance(payload, dict):
            return
        now = datetime.now(UTC)
        for name, row in payload.items():
            clean_name = str(name or "").strip()
            if not clean_name:
                continue
            key = self._user_key(clean_name)
            policy = self.known_users.get(key)
            if policy is None:
                policy = UserPolicy(name=clean_name, first_seen=now, last_seen=now)
                self.known_users[key] = policy
            else:
                policy.name = clean_name
            if "enabled" in row:
                policy.enabled = bool(row.get("enabled"))
            mode = row.get("mode")
            if mode in {"normal", "register_only", "one_event_only"}:
                policy.mode = mode
            if "allowed_activities" in row:
                policy.allowed_activities = self._normalize_allowed_activities(row.get("allowed_activities"))
            self._sync_allowed_user(policy.name)

    def _apply_allowed_users_registry(self) -> None:
        raw_users = self.options.get(CONF_ALLOWED_USERS, []) or []
        if not isinstance(raw_users, list):
            return
        now = datetime.now(UTC)
        for raw_name in raw_users:
            clean_name = str(raw_name or "").strip()
            if not clean_name:
                continue
            key = self._user_key(clean_name)
            policy = self.known_users.get(key)
            if policy is None:
                self.known_users[key] = UserPolicy(
                    name=clean_name,
                    enabled=True,
                    first_seen=now,
                    last_seen=now,
                    first_event_consumed=False,
                    mode="normal",
                )
            else:
                policy.name = clean_name
            self._sync_allowed_user(clean_name)

    @staticmethod
    def _normalize_allowed_activities(value) -> list[str] | None:
        if value in (None, "", "inherit"):
            return None
        if isinstance(value, str):
            items = [part.strip().lower() for part in value.split(",")]
        elif isinstance(value, list):
            items = [str(part).strip().lower() for part in value]
        else:
            return None
        allowed = [item for item in items if item in {"running", "walking", "cycling", "strength", "swimming", "kayak", "rowing", "other"}]
        return sorted(set(allowed)) or None

    def _policy_for_user(self, user: str | None) -> UserPolicy | None:
        if not user:
            return None
        return self.known_users.get(self._user_key(user))

    def _effective_activity_allowed(self, user: str | None, activity: str | None) -> bool:
        normalized = normalize_activity(activity)
        policy = self._policy_for_user(user)
        if policy and policy.allowed_activities is not None:
            return normalized in policy.allowed_activities
        filt = self.options.get(CONF_ACTIVITY_FILTER, "all")
        return filt == "all" or normalized == filt

    def _activity_policy_mode(self, user: str | None) -> str:
        policy = self._policy_for_user(user)
        if policy and policy.allowed_activities is not None:
            return "custom"
        return "inherit_global"

    def _effective_activity_filter(self, user: str | None):
        policy = self._policy_for_user(user)
        if policy and policy.allowed_activities is not None:
            return policy.allowed_activities
        return self.options.get(CONF_ACTIVITY_FILTER, "all")

    def _prune_duplicate_waiting_sessions_for_user(self, new_session: LiveTrackSession) -> None:
        new_user = self._user_key(new_session.garmin_user)
        if not new_user:
            return
        for sid, coord in list(self.sessions.items()):
            existing = coord.session
            if self._user_key(existing.garmin_user) != new_user:
                continue
            if existing.identity.session_id == new_session.identity.session_id:
                continue
            if (
                existing.trackpoint_count == 0
                and new_session.trackpoint_count == 0
                and existing.status == LiveTrackStatus.WAITING_FOR_TRACKPOINT
                and new_session.status == LiveTrackStatus.WAITING_FOR_TRACKPOINT
            ):
                if existing.first_seen > new_session.first_seen:
                    new_session.status = LiveTrackStatus.STOPPED
                    new_session.actual_end = datetime.now(UTC)
                    return
                existing.status = LiveTrackStatus.STOPPED
                existing.actual_end = datetime.now(UTC)
                self.ended_sessions[sid] = existing
                self.sessions.pop(sid, None)

    def _register_services(self):
        async def _add(call):
            await self.async_add_url(call.data.get("url", ""), LiveTrackSource.SERVICE)

        async def _stop(call):
            sid = call.data.get("session_id")
            sid_hash = call.data.get("session_id_hash")
            if sid:
                await self.async_stop_session(sid)
                return
            if sid_hash:
                for candidate in self.sessions:
                    if stable_session_hash(candidate) == sid_hash:
                        await self.async_stop_session(candidate)
                        return

        async def _clear(call):
            await self.async_clear_ended(call.data.get("older_than_hours"))

        async def _reload(call):
            await self.async_reload_users()

        async def _refresh_session(call):
            await self.async_refresh_session(
                call.data.get("session_id"),
                call.data.get("session_id_hash"),
            )

        async def _refresh_all(call):
            await self.async_refresh_all()

        async def _cleanup_legacy(call):
            await self.async_cleanup_legacy_entities()

        async def _set_user_policy(call):
            await self.async_set_user_policy(
                call.data.get("user", ""),
                call.data.get("enabled"),
                call.data.get("mode"),
                call.data.get("allowed_activities"),
            )

        async def _remove_user(call):
            await self.async_remove_user(call.data.get("user", ""))

        async def _list_users(call):
            users = await self.async_list_users()
            self.hass.bus.async_fire(
                "garmin_livetrack_users_listed",
                {"count": len(users), "users": users},
            )
            return {"count": len(users), "users": users}

        if not self.hass.services.has_service("garmin_livetrack", SERVICE_ADD_URL):
            self.hass.services.async_register("garmin_livetrack", SERVICE_ADD_URL, _add)
        if not self.hass.services.has_service("garmin_livetrack", SERVICE_STOP_SESSION):
            self.hass.services.async_register("garmin_livetrack", SERVICE_STOP_SESSION, _stop)
        if not self.hass.services.has_service("garmin_livetrack", SERVICE_CLEAR_ENDED):
            self.hass.services.async_register("garmin_livetrack", SERVICE_CLEAR_ENDED, _clear)
        if not self.hass.services.has_service("garmin_livetrack", SERVICE_RELOAD_USERS):
            self.hass.services.async_register("garmin_livetrack", SERVICE_RELOAD_USERS, _reload)
        if not self.hass.services.has_service("garmin_livetrack", SERVICE_REFRESH_SESSION):
            self.hass.services.async_register("garmin_livetrack", SERVICE_REFRESH_SESSION, _refresh_session)
        if not self.hass.services.has_service("garmin_livetrack", SERVICE_REFRESH_ALL):
            self.hass.services.async_register("garmin_livetrack", SERVICE_REFRESH_ALL, _refresh_all)
        if not self.hass.services.has_service("garmin_livetrack", SERVICE_CLEANUP_LEGACY_ENTITIES):
            self.hass.services.async_register("garmin_livetrack", SERVICE_CLEANUP_LEGACY_ENTITIES, _cleanup_legacy)
        if not self.hass.services.has_service("garmin_livetrack", SERVICE_SET_USER_POLICY):
            self.hass.services.async_register("garmin_livetrack", SERVICE_SET_USER_POLICY, _set_user_policy)
        if not self.hass.services.has_service("garmin_livetrack", SERVICE_REMOVE_USER):
            self.hass.services.async_register("garmin_livetrack", SERVICE_REMOVE_USER, _remove_user)
        if not self.hass.services.has_service("garmin_livetrack", SERVICE_LIST_USERS):
            self.hass.services.async_register(
                "garmin_livetrack",
                SERVICE_LIST_USERS,
                _list_users,
                supports_response=SupportsResponse.ONLY,
            )

    async def _update_imap_listener(self):
        enabled = self.options.get(CONF_LISTEN_TO_IMAP_EVENTS, True)
        if self.unsub_imap_listener:
            self.unsub_imap_listener()
            self.unsub_imap_listener = None
        if enabled:

            @callback
            def _listener(event: Event) -> None:
                self.hass.async_create_task(self.async_handle_imap_event(event))

            self.unsub_imap_listener = self.hass.bus.async_listen(EVENT_IMAP_CONTENT, _listener)
