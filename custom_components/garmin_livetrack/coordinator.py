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
    CONF_ENABLE_NOTIFICATIONS,
    CONF_FINALIZATION_MINUTES,
    CONF_IOS_NOTIFICATION_STYLE,
    CONF_LISTEN_TO_IMAP_EVENTS,
    CONF_MAX_RUNTIME_HOURS,
    CONF_NOTIFY_SERVICE,
    CONF_STALE_MINUTES,
    CONF_STRICT_USERS,
    CONF_UPDATE_INTERVAL,
    CONF_USER_POLICIES,
    EVENT_IMAP_CONTENT,
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
    SERVICE_TEST_NOTIFICATION,
)
from .models import (
    LiveTrackIdentity,
    LiveTrackPoint,
    LiveTrackSession,
    LiveTrackSource,
    LiveTrackStatus,
    extract_event_types,
    normalize_activity,
    parse_garmin_datetime,
    stable_session_hash,
)

URL_RE = re.compile(r"https://livetrack\.garmin\.com/session/[^\"'>\s]+", re.IGNORECASE)
_LOGGER = logging.getLogger(__name__)

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
    enable_notifications: bool | None = None
    notify_service: str | None = None
    ios_notification_style: bool | None = None
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

    async def async_start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self.manager.startup_debug[f"poller_start_{stable_session_hash(self.session.identity.session_id)}"] = datetime.now(UTC).isoformat()
        _LOGGER.warning(
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
            interval = int(self.manager.options.get(CONF_UPDATE_INTERVAL, 60))
            if self.session.status == LiveTrackStatus.WAITING_FOR_TRACKPOINT:
                initial_wait_minutes = int(self.manager.options.get("initial_trackpoint_wait_minutes", 10))
                within_initial = (datetime.now(UTC) - self.session.first_seen) < timedelta(minutes=initial_wait_minutes)
                if within_initial:
                    interval = min(interval, 10)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=max(30, interval))
            except asyncio.TimeoutError:
                pass

    async def _refresh_once(self) -> None:
        self.session.status = LiveTrackStatus.FETCHING
        fetch = await self.manager.client.fetch(self.session.identity)
        self.session.last_fetch = fetch.fetched_at
        self.last_page_status = fetch.page_status
        self.last_api_status = fetch.api_status
        self.last_source_branch = str(fetch.source.get("trackpoints_source", "none")) if isinstance(fetch.source, dict) else "none"
        self.session.errors.extend(fetch.errors[-3:])
        if fetch.errors:
            self.manager.last_error = fetch.errors[-1].code
            codes = {e.code for e in fetch.errors}
            if "missing_session" in codes or "missing_trackpoints" in codes:
                self.manager.shape_change_count += 1
                if self.manager.shape_change_count >= 3:
                    self.manager.shape_change_suspected = True
            else:
                self.manager.shape_change_count = 0
        elif self.manager.shape_change_count:
            self.manager.shape_change_count = max(0, self.manager.shape_change_count - 1)

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
                or self.session.activity_type
            )
            if activity_raw:
                self.session.activity_type = normalize_activity(activity_raw)
            self.session.start = parse_garmin_datetime(fetch.session.get("start")) or self.session.start
            self.session.expected_end = parse_garmin_datetime(fetch.session.get("end")) or self.session.expected_end
            self.session.trackpoint_count = fetch.trackpoint_count
            self.session.last_point = self._to_point(fetch.last_trackpoint)

            if self.session.trackpoint_count > 0:
                self.session.status = LiveTrackStatus.ACTIVE
            else:
                self.session.status = LiveTrackStatus.WAITING_FOR_TRACKPOINT

            if first_success and not self._logged_first_success:
                self._logged_first_success = True
                self.manager.startup_debug[f"first_success_{stable_session_hash(self.session.identity.session_id)}"] = fetch.fetched_at.isoformat()
                _LOGGER.warning(
                    "Garmin LiveTrack startup diag: first fetch success for session=%s status=%s trackpoints=%s source=%s user=%s",
                    stable_session_hash(self.session.identity.session_id),
                    self.session.status.value,
                    self.session.trackpoint_count,
                    self.last_source_branch,
                    self.session.garmin_user or "unknown",
                )

            if await self._handle_no_progress(now=fetch.fetched_at):
                return
            await self._handle_end_state(first_success=first_success)
        else:
            stale_cutoff = timedelta(minutes=int(self.manager.options.get(CONF_STALE_MINUTES, 15)))
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
            {
                "session_id_hash": stable_session_hash(self.session.identity.session_id),
                "status": self.session.status.value,
            },
        )

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
        stale_cutoff = timedelta(minutes=int(self.manager.options.get(CONF_STALE_MINUTES, 15)))
        initial_wait = timedelta(minutes=int(self.manager.options.get("initial_trackpoint_wait_minutes", 10)))
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

    @staticmethod
    def _session_key(session_id: str) -> str:
        return str(session_id).strip().lower()

    @staticmethod
    def _user_key(user: str | None) -> str:
        return str(user or "").strip().lower()

    def _notify_listeners(self) -> None:
        for listener in list(self._listeners):
            listener()

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
        await self.async_load_storage()
        self._apply_option_user_policies()
        self._register_services()
        await self._update_imap_listener()

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
                self.hass.bus.async_fire(EVENT_SESSION_REJECTED, {"session_id_hash": stable_session_hash(sid), "reason": session.rejected_reason})
                return AddUrlResult(False, session.status, stable_session_hash(sid), session.rejected_reason or "rejected")

            self._prune_duplicate_waiting_sessions_for_user(session)
            self.sessions[sid] = coord
            await coord.async_start()
            self.hass.bus.async_fire(EVENT_SESSION_ADDED, {"session_id_hash": stable_session_hash(sid), "source": source.value})
            await self.async_notify_start(session)
            await self.async_save_storage()
            self._notify_listeners()
            return AddUrlResult(True, session.status, stable_session_hash(sid), "added")

    async def async_finalize_session(self, coord: LiveTrackSessionCoordinator, reason: str) -> None:
        sid = self._session_key(coord.session.identity.session_id)
        self.sessions.pop(sid, None)
        self.ended_sessions[sid] = coord.session
        coord.session.actual_end = coord.session.actual_end or datetime.now(UTC)
        await self.async_notify_end(coord.session, reason)
        self.hass.bus.async_fire(EVENT_SESSION_ENDED, {"session_id_hash": stable_session_hash(sid), "reason": reason})
        await self.async_save_storage()
        self._notify_listeners()

    async def async_stop_session(self, session_id: str, reason: str = "manual"):
        sid = self._session_key(session_id)
        coord = self.sessions.pop(sid, None)
        if not coord:
            return
        await coord.async_stop(reason)
        coord.session.status = LiveTrackStatus.STOPPED
        coord.session.actual_end = datetime.now(UTC)
        self.ended_sessions[sid] = coord.session
        self.hass.bus.async_fire(EVENT_SESSION_UPDATED, {"session_id_hash": stable_session_hash(sid), "status": LiveTrackStatus.STOPPED.value, "reason": reason})
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
                session = LiveTrackSession(identity, row.get("garmin_user"), row.get("activity_type"), parse_garmin_datetime(row.get("start")), parse_garmin_datetime(row.get("expected_end")), None, first_seen, None, None, None, 0, LiveTrackStatus(row.get("status", LiveTrackStatus.DISCOVERED.value)))
                session.status = self._persistable_status(session.status)
                coord = LiveTrackSessionCoordinator(self, session)
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
                    start=parse_garmin_datetime(row.get("start")),
                    expected_end=parse_garmin_datetime(row.get("expected_end")),
                    actual_end=None,
                    first_seen=parse_garmin_datetime(row.get("first_seen")) or datetime.now(UTC),
                    last_fetch=None,
                    last_success=None,
                    last_point=None,
                    trackpoint_count=0,
                    status=status,
                    notification_started_sent=row.get("notification_started_sent", False),
                    notification_ended_sent=row.get("notification_ended_sent", False),
                )
                self._prune_duplicate_waiting_sessions_for_user(session)
                coord = LiveTrackSessionCoordinator(self, session)
                self.sessions[sid] = coord
                self.startup_debug[f"restored_session_{stable_session_hash(sid)}"] = session.identity.source.value
                _LOGGER.warning(
                    "Garmin LiveTrack startup diag: restored session=%s source=%s user=%s status=%s",
                    stable_session_hash(sid),
                    session.identity.source.value,
                    session.garmin_user or "unknown",
                    session.status.value,
                )
                restored += 1
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
            active_sessions.append(
                {
                    "session_id": sid,
                    "token": c.session.identity.token,
                    "redacted_url": c.session.identity.redacted_url,
                    "source": c.session.identity.source.value,
                    "first_seen": c.session.first_seen.isoformat(),
                    "garmin_user": c.session.garmin_user,
                    "activity_type": c.session.activity_type,
                    "start": c.session.start.isoformat() if c.session.start else None,
                    "expected_end": c.session.expected_end.isoformat() if c.session.expected_end else None,
                    "status": self._persistable_status(c.session.status).value,
                    "notification_started_sent": c.session.notification_started_sent,
                    "notification_ended_sent": c.session.notification_ended_sent,
                }
            )

        await self.store.async_save(
            {
                "active_sessions": active_sessions,
                "known_users": {
                    name: {
                        "name": p.name,
                        "enabled": p.enabled,
                        "first_seen": p.first_seen.isoformat() if p.first_seen else None,
                        "last_seen": p.last_seen.isoformat() if p.last_seen else None,
                        "first_event_consumed": p.first_event_consumed,
                        "mode": p.mode,
                        "enable_notifications": p.enable_notifications,
                        "notify_service": p.notify_service,
                        "ios_notification_style": p.ios_notification_style,
                        "allowed_activities": p.allowed_activities,
                    }
                    for name, p in self.known_users.items()
                },
            }
        )

    async def async_load_storage(self):
        raw = await self.store.async_load() or {}
        data = self._storage_payload(raw)
        known_users: dict[str, UserPolicy] = {}
        for raw_name, value in data.get("known_users", {}).items():
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
                enable_notifications=value.get("enable_notifications"),
                notify_service=value.get("notify_service"),
                ios_notification_style=value.get("ios_notification_style"),
                allowed_activities=self._normalize_allowed_activities(value.get("allowed_activities")),
            )
        self.known_users = known_users

    async def async_reload_users(self):
        await self.async_load_storage()
        self._apply_option_user_policies()

    async def async_send_test_notification(self):
        if not self.options.get(CONF_ENABLE_NOTIFICATIONS, True):
            return
        target = self.options.get(CONF_NOTIFY_SERVICE, "notify.notify")
        if "." not in target:
            return
        domain, service = target.split(".", 1)
        await self.hass.services.async_call(domain, service, {"message": "Garmin LiveTrack test notification"}, blocking=False)

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
        enable_notifications: bool | None = None,
        notify_service: str | None = None,
        ios_notification_style: bool | None = None,
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
        if enable_notifications is not None:
            policy.enable_notifications = bool(enable_notifications)
        if notify_service is not None:
            cleaned_notify = str(notify_service).strip()
            if cleaned_notify and "." not in cleaned_notify:
                self.last_error = "invalid_notify_service"
                return False
            policy.notify_service = cleaned_notify or None
        if ios_notification_style is not None:
            policy.ios_notification_style = bool(ios_notification_style)
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
                    "enable_notifications": policy.enable_notifications,
                    "notify_service": "configured" if policy.notify_service else None,
                    "ios_notification_style": policy.ios_notification_style,
                    "notification_policy_mode": self._notification_policy_mode(policy.name),
                    "notify_service_policy_mode": self._notify_service_policy_mode(policy.name),
                    "ios_notification_style_policy_mode": self._ios_notification_style_policy_mode(policy.name),
                    "allowed_activities": policy.allowed_activities,
                    "activity_policy_mode": self._activity_policy_mode(policy.name),
                    "effective_enable_notifications": self._effective_notifications_enabled(policy.name),
                    "effective_notify_service": self._effective_notify_service(policy.name),
                    "effective_ios_notification_style": self._effective_ios_notification_style(policy.name),
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
            if "enable_notifications" in row:
                value = row.get("enable_notifications")
                policy.enable_notifications = None if value is None else bool(value)
            if "notify_service" in row:
                notify_service = str(row.get("notify_service") or "").strip()
                policy.notify_service = notify_service or None
            if "ios_notification_style" in row:
                value = row.get("ios_notification_style")
                policy.ios_notification_style = None if value is None else bool(value)
            if "allowed_activities" in row:
                policy.allowed_activities = self._normalize_allowed_activities(row.get("allowed_activities"))
            self._sync_allowed_user(policy.name)

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
        allowed = [item for item in items if item in {"running", "walking", "cycling", "strength", "swimming", "other"}]
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

    def _notification_policy_mode(self, user: str | None) -> str:
        policy = self._policy_for_user(user)
        if policy and policy.enable_notifications is not None:
            return "custom"
        return "inherit_global"

    def _notify_service_policy_mode(self, user: str | None) -> str:
        policy = self._policy_for_user(user)
        if policy and policy.notify_service:
            return "custom"
        return "inherit_global"

    def _ios_notification_style_policy_mode(self, user: str | None) -> str:
        policy = self._policy_for_user(user)
        if policy and policy.ios_notification_style is not None:
            return "custom"
        return "inherit_global"

    def _effective_notifications_enabled(self, user: str | None) -> bool:
        policy = self._policy_for_user(user)
        if policy and policy.enable_notifications is not None:
            return bool(policy.enable_notifications)
        return bool(self.options.get(CONF_ENABLE_NOTIFICATIONS, True))

    def _effective_notify_service(self, user: str | None) -> str:
        policy = self._policy_for_user(user)
        if policy and policy.notify_service:
            return policy.notify_service
        return str(self.options.get(CONF_NOTIFY_SERVICE, "notify.notify"))

    def _effective_ios_notification_style(self, user: str | None) -> bool:
        policy = self._policy_for_user(user)
        if policy and policy.ios_notification_style is not None:
            return bool(policy.ios_notification_style)
        return bool(self.options.get(CONF_IOS_NOTIFICATION_STYLE, False))

    async def async_notify_start(self, session: LiveTrackSession):
        if not self._effective_notifications_enabled(session.garmin_user) or session.notification_started_sent:
            return
        target = self._effective_notify_service(session.garmin_user)
        if "." not in target:
            self.last_error = "invalid_notify_service"
            return
        domain, service = target.split(".", 1)
        payload = {"message": f"LiveTrack started: {session.garmin_user or 'Unknown'} ({session.activity_type or 'unknown'})"}
        if self._effective_ios_notification_style(session.garmin_user) and session.last_point:
            payload["data"] = {
                "push": {"sound": {"name": "default", "critical": 0, "volume": 1.0}},
                "url": "/lovelace",
            }
        await self.hass.services.async_call(domain, service, payload, blocking=False)
        session.notification_started_sent = True

    async def async_notify_end(self, session: LiveTrackSession, reason: str):
        if not self._effective_notifications_enabled(session.garmin_user) or session.notification_ended_sent:
            return
        target = self._effective_notify_service(session.garmin_user)
        if "." not in target:
            self.last_error = "invalid_notify_service"
            return
        domain, service = target.split(".", 1)
        payload = {"message": f"LiveTrack ended: {session.garmin_user or 'Unknown'} ({session.activity_type or 'unknown'}) - {reason}"}
        if self._effective_ios_notification_style(session.garmin_user):
            payload["data"] = {
                "push": {"sound": {"name": "default", "critical": 0, "volume": 1.0}},
                "url": "/lovelace",
            }
        await self.hass.services.async_call(domain, service, payload, blocking=False)
        session.notification_ended_sent = True

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

        async def _test(call):
            await self.async_send_test_notification()

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
                call.data.get("enable_notifications"),
                call.data.get("notify_service"),
                call.data.get("ios_notification_style"),
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
        if not self.hass.services.has_service("garmin_livetrack", SERVICE_TEST_NOTIFICATION):
            self.hass.services.async_register("garmin_livetrack", SERVICE_TEST_NOTIFICATION, _test)
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
