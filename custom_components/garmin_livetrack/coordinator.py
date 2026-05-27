from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable

from homeassistant.core import Event, callback

from .const import (
    CONF_ACCEPT_FIRST_SEEN_USERS,
    CONF_ACTIVITY_FILTER,
    CONF_ALLOWED_USERS,
    CONF_ENABLE_NOTIFICATIONS,
    CONF_FINALIZATION_MINUTES,
    CONF_LISTEN_TO_IMAP_EVENTS,
    CONF_MAX_RUNTIME_HOURS,
    CONF_NOTIFY_SERVICE,
    CONF_STALE_MINUTES,
    CONF_STRICT_USERS,
    CONF_UPDATE_INTERVAL,
    EVENT_IMAP_CONTENT,
    EVENT_SESSION_ADDED,
    EVENT_SESSION_ENDED,
    EVENT_SESSION_REJECTED,
    EVENT_SESSION_UPDATED,
    SERVICE_ADD_URL,
    SERVICE_CLEAR_ENDED,
    SERVICE_RELOAD_USERS,
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
    parse_garmin_datetime,
    stable_session_hash,
)

URL_RE = re.compile(r"https://livetrack\.garmin\.com/session/[^\"'>\s]+", re.IGNORECASE)

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


class LiveTrackSessionCoordinator:
    def __init__(self, manager: GarminLiveTrackManager, session: LiveTrackSession) -> None:
        self.manager = manager
        self.session = session
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.end_reason: str | None = None

    async def async_start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
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
            await self._refresh_once()
            if self.session.status in {
                LiveTrackStatus.ENDED,
                LiveTrackStatus.EXPIRED,
                LiveTrackStatus.STALE,
                LiveTrackStatus.STOPPED,
                LiveTrackStatus.GARMIN_ERROR,
            }:
                break
            interval = int(self.manager.options.get(CONF_UPDATE_INTERVAL, 60))
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=max(15, interval))
            except asyncio.TimeoutError:
                pass

    async def _refresh_once(self) -> None:
        self.session.status = LiveTrackStatus.FETCHING
        fetch = await self.manager.client.fetch(self.session.identity)
        self.session.last_fetch = fetch.fetched_at
        self.session.errors.extend(fetch.errors[-3:])
        if fetch.errors:
            self.manager.last_error = fetch.errors[-1].code

        if fetch.ok:
            self.session.last_success = fetch.fetched_at
            self.session.garmin_user = (fetch.session.get("userDisplayName") or "").strip() or self.session.garmin_user
            self.session.activity_type = fetch.session.get("activityType") or fetch.session.get("activity") or self.session.activity_type
            self.session.start = parse_garmin_datetime(fetch.session.get("start")) or self.session.start
            self.session.expected_end = parse_garmin_datetime(fetch.session.get("end")) or self.session.expected_end
            self.session.trackpoint_count = fetch.trackpoint_count
            self.session.last_point = self._to_point(fetch.last_trackpoint)

            if self.session.trackpoint_count > 0:
                self.session.status = LiveTrackStatus.ACTIVE
            else:
                self.session.status = LiveTrackStatus.WAITING_FOR_TRACKPOINT

            await self._handle_end_state()
        else:
            stale_cutoff = timedelta(minutes=int(self.manager.options.get(CONF_STALE_MINUTES, 15)))
            if self.session.last_success and (datetime.now(UTC) - self.session.last_success) > stale_cutoff:
                self.session.status = LiveTrackStatus.STALE
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

    async def _handle_end_state(self) -> None:
        now = datetime.now(UTC)
        max_runtime_hours = int(self.manager.options.get(CONF_MAX_RUNTIME_HOURS, 23))
        if self.session.start and now - self.session.start > timedelta(hours=max_runtime_hours):
            self.session.status = LiveTrackStatus.EXPIRED
            self.end_reason = "max_runtime"
            await self.manager.async_finalize_session(self, self.end_reason)
            return

        event_types = set(self.session.last_point.event_types if self.session.last_point else [])
        ended_by_event = "END" in event_types
        ended_by_time = bool(self.session.expected_end and self.session.expected_end < now)
        if ended_by_event or ended_by_time:
            if self.session.status != LiveTrackStatus.ENDING:
                self.session.status = LiveTrackStatus.ENDING
                self.end_reason = "end_event" if ended_by_event else "session_end"
                if not hasattr(self, "_ending_since"):
                    self._ending_since = now
            finalization = timedelta(minutes=int(self.manager.options.get(CONF_FINALIZATION_MINUTES, 10)))
            if now - self._ending_since >= finalization:
                self.session.status = LiveTrackStatus.ENDED
                self.session.actual_end = now
                await self.manager.async_finalize_session(self, self.end_reason or "ended")

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
        self._listeners: list[Callable[[], None]] = []

    @staticmethod
    def _session_key(session_id: str) -> str:
        return str(session_id).strip().lower()

    @staticmethod
    def _user_key(user: str | None) -> str:
        return str(user or "").strip().lower()

    def _notify_listeners(self) -> None:
        for listener in list(self._listeners):
            listener()

    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)

        def _unsub() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _unsub

    async def async_setup(self):
        await self.async_load_storage()
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
        data = await self.store.async_load() or {}
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
                coord = LiveTrackSessionCoordinator(self, session)
                await coord.async_first_fetch()
                if session.status in ACTIVE_STATES:
                    self._prune_duplicate_waiting_sessions_for_user(session)
                    self.sessions[identity.session_id] = coord
                    await coord.async_start()
        self._notify_listeners()

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
                    "status": c.session.status.value,
                    "notification_started_sent": c.session.notification_started_sent,
                    "notification_ended_sent": c.session.notification_ended_sent,
                }
            )

        await self.store.async_save(
            {
                "active_sessions": active_sessions,
                "known_users": {name: {"name": p.name, "enabled": p.enabled} for name, p in self.known_users.items()},
            }
        )

    async def async_load_storage(self):
        data = await self.store.async_load() or {}
        self.known_users = {n: UserPolicy(name=v.get("name", n), enabled=v.get("enabled", True)) for n, v in data.get("known_users", {}).items()}

    async def async_reload_users(self):
        await self.async_load_storage()

    async def async_send_test_notification(self):
        if not self.options.get(CONF_ENABLE_NOTIFICATIONS, True):
            return
        target = self.options.get(CONF_NOTIFY_SERVICE, "notify.notify")
        if "." not in target:
            return
        domain, service = target.split(".", 1)
        await self.hass.services.async_call(domain, service, {"message": "Garmin LiveTrack test notification"}, blocking=False)

    async def async_handle_imap_event(self, event: Event):
        text = " ".join(str(event.data.get(k, "")) for k in ("custom", "text", "body", "subject"))
        text = text.replace("=\r\n", "").replace("=\n", "")
        m = URL_RE.search(text)
        if m:
            await self.async_add_url(m.group(0), LiveTrackSource.IMAP)

    async def async_validate_session_policy(self, session: LiveTrackSession):
        strict = self.options.get(CONF_STRICT_USERS, False)
        user = (session.garmin_user or "").strip()
        if strict:
            allowed = {u.strip() for u in self.options.get(CONF_ALLOWED_USERS, []) if u.strip()}
            if user not in allowed:
                if self.options.get(CONF_ACCEPT_FIRST_SEEN_USERS, False) and user:
                    self.known_users[user] = UserPolicy(name=user, enabled=True, first_seen=datetime.now(UTC), last_seen=datetime.now(UTC))
                    current = self.options.get(CONF_ALLOWED_USERS, [])
                    if user not in current:
                        self.options[CONF_ALLOWED_USERS] = [*current, user]
                else:
                    session.status = LiveTrackStatus.REJECTED_USER
                    session.rejected_reason = "rejected_user"
                    return False
        filt = self.options.get(CONF_ACTIVITY_FILTER, "all")
        activity = str(session.activity_type or "other").strip().lower()
        if filt != "all" and activity != filt:
            session.status = LiveTrackStatus.REJECTED_ACTIVITY
            session.rejected_reason = "rejected_activity"
            return False
        return True

    async def async_notify_start(self, session: LiveTrackSession):
        if not self.options.get(CONF_ENABLE_NOTIFICATIONS, True) or session.notification_started_sent:
            return
        target = self.options.get(CONF_NOTIFY_SERVICE, "notify.notify")
        if "." not in target:
            self.last_error = "invalid_notify_service"
            return
        domain, service = target.split(".", 1)
        payload = {"message": f"LiveTrack started: {session.garmin_user or 'Unknown'} ({session.activity_type or 'unknown'})"}
        await self.hass.services.async_call(domain, service, payload, blocking=False)
        session.notification_started_sent = True

    async def async_notify_end(self, session: LiveTrackSession, reason: str):
        if not self.options.get(CONF_ENABLE_NOTIFICATIONS, True) or session.notification_ended_sent:
            return
        target = self.options.get(CONF_NOTIFY_SERVICE, "notify.notify")
        if "." not in target:
            self.last_error = "invalid_notify_service"
            return
        domain, service = target.split(".", 1)
        payload = {"message": f"LiveTrack ended: {session.garmin_user or 'Unknown'} ({session.activity_type or 'unknown'}) - {reason}"}
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
