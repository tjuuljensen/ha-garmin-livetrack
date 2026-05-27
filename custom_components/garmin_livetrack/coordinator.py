from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from homeassistant.core import Event, callback

from .const import (
    CONF_ACCEPT_FIRST_SEEN_USERS,
    CONF_ACTIVITY_FILTER,
    CONF_ALLOWED_USERS,
    CONF_ENABLE_NOTIFICATIONS,
    CONF_LISTEN_TO_IMAP_EVENTS,
    CONF_NOTIFY_SERVICE,
    EVENT_IMAP_CONTENT,
    EVENT_SESSION_ADDED,
    EVENT_SESSION_REJECTED,
    EVENT_SESSION_UPDATED,
    SERVICE_ADD_URL,
    SERVICE_CLEAR_ENDED,
    SERVICE_RELOAD_USERS,
    SERVICE_STOP_SESSION,
    SERVICE_TEST_NOTIFICATION,
)
from .models import LiveTrackIdentity, LiveTrackSession, LiveTrackSource, LiveTrackStatus, parse_garmin_datetime, stable_session_hash

URL_RE = re.compile(r"https://livetrack\.garmin\.com/session/[^\"'>\s]+", re.IGNORECASE)


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
    def __init__(self, manager, session: LiveTrackSession):
        self.manager = manager
        self.session = session

    async def async_first_fetch(self):
        self.session.status = LiveTrackStatus.FETCHING
        fetch = await self.manager.client.fetch(self.session.identity)
        self.session.last_fetch = fetch.fetched_at
        self.session.errors.extend(fetch.errors)
        if fetch.ok:
            self.session.last_success = fetch.fetched_at
            self.session.garmin_user = fetch.session.get("userDisplayName")
            self.session.activity_type = fetch.session.get("activityType") or fetch.session.get("activity")
            self.session.start = parse_garmin_datetime(fetch.session.get("start"))
            self.session.expected_end = parse_garmin_datetime(fetch.session.get("end"))
            self.session.trackpoint_count = fetch.trackpoint_count
            self.session.status = LiveTrackStatus.ACTIVE if fetch.trackpoint_count else LiveTrackStatus.WAITING_FOR_TRACKPOINT
        else:
            self.session.status = LiveTrackStatus.GARMIN_ERROR


class GarminLiveTrackManager:
    def __init__(self, hass, client, store, options):
        self.hass = hass
        self.client = client
        self.store = store
        self.options = options
        self.sessions = {}
        self.ended_sessions = {}
        self.known_users = {}
        self.unsub_imap_listener = None
        self.lock = asyncio.Lock()
        self.last_error = None

    async def async_setup(self):
        await self.async_load_storage()
        self._register_services()
        await self._update_imap_listener()

    async def async_unload(self):
        if self.unsub_imap_listener:
            self.unsub_imap_listener()
            self.unsub_imap_listener = None
        await self.async_save_storage()

    async def async_add_url(self, url: str, source: LiveTrackSource) -> AddUrlResult:
        async with self.lock:
            try:
                identity = self.client.parse_livetrack_identity(url=url, source=source)
            except ValueError as err:
                return AddUrlResult(False, LiveTrackStatus.INVALID_URL, message=str(err))
            sid = identity.session_id
            if sid in self.sessions:
                return AddUrlResult(True, LiveTrackStatus.DUPLICATE, stable_session_hash(sid), "duplicate")
            session = LiveTrackSession(identity, None, None, None, None, None, datetime.now(UTC), None, None, None, 0, LiveTrackStatus.DISCOVERED)
            coord = LiveTrackSessionCoordinator(self, session)
            await coord.async_first_fetch()
            if session.status == LiveTrackStatus.GARMIN_ERROR:
                self.hass.bus.async_fire(EVENT_SESSION_REJECTED, {"session_id_hash": stable_session_hash(sid), "reason": "garmin_error"})
                return AddUrlResult(False, session.status, stable_session_hash(sid), "garmin_error")
            if not await self.async_validate_session_policy(session):
                self.hass.bus.async_fire(EVENT_SESSION_REJECTED, {"session_id_hash": stable_session_hash(sid), "reason": session.rejected_reason})
                return AddUrlResult(False, session.status, stable_session_hash(sid), session.rejected_reason or "rejected")
            self.sessions[sid] = coord
            self.hass.bus.async_fire(EVENT_SESSION_ADDED, {"session_id_hash": stable_session_hash(sid), "source": source.value})
            self.hass.bus.async_fire(EVENT_SESSION_UPDATED, {"session_id_hash": stable_session_hash(sid), "status": session.status.value})
            await self.async_notify_start(session)
            await self.async_save_storage()
            return AddUrlResult(True, session.status, stable_session_hash(sid), "added")

    async def async_stop_session(self, session_id: str, reason: str = "manual"):
        coord = self.sessions.pop(session_id, None)
        if not coord:
            return
        coord.session.status = LiveTrackStatus.STOPPED
        coord.session.actual_end = datetime.now(UTC)
        self.ended_sessions[session_id] = coord.session
        self.hass.bus.async_fire(EVENT_SESSION_UPDATED, {"session_id_hash": stable_session_hash(session_id), "status": LiveTrackStatus.STOPPED.value, "reason": reason})
        await self.async_save_storage()

    async def async_clear_ended(self, older_than_hours=None):
        count = len(self.ended_sessions)
        self.ended_sessions.clear()
        await self.async_save_storage()
        return count

    async def async_recover_sessions(self):
        data = await self.store.async_load() or {}
        for row in data.get("active_sessions", []):
            identity = LiveTrackIdentity(
                session_id=row["session_id"],
                token=row["token"],
                canonical_url=f"https://livetrack.garmin.com/session/{row['session_id']}/token/{row['token']}",
                redacted_url=row.get("redacted_url", ""),
                source=LiveTrackSource(row.get("source", LiveTrackSource.RECOVERY.value)),
            )
            session = LiveTrackSession(identity, row.get("garmin_user"), row.get("activity_type"), parse_garmin_datetime(row.get("start")), parse_garmin_datetime(row.get("expected_end")), None, parse_garmin_datetime(row.get("first_seen")) or datetime.now(UTC), None, None, None, 0, LiveTrackStatus(row.get("status", LiveTrackStatus.DISCOVERED.value)))
            coord = LiveTrackSessionCoordinator(self, session)
            await coord.async_first_fetch()
            if session.status in {LiveTrackStatus.ACTIVE, LiveTrackStatus.WAITING_FOR_TRACKPOINT}:
                self.sessions[identity.session_id] = coord

    async def async_save_storage(self):
        await self.store.async_save({
            "active_sessions": [
                {
                    "session_id": c.session.identity.session_id,
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
                for c in self.sessions.values()
            ],
            "known_users": {name: {"name": p.name, "enabled": p.enabled} for name, p in self.known_users.items()},
        })

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
        strict = self.options.get("strict_users", False)
        user = (session.garmin_user or "").strip()
        if strict:
            allowed = {u.strip() for u in self.options.get(CONF_ALLOWED_USERS, []) if u.strip()}
            if user not in allowed:
                if self.options.get(CONF_ACCEPT_FIRST_SEEN_USERS, False) and user:
                    self.known_users[user] = UserPolicy(name=user, enabled=True, first_seen=datetime.now(UTC), last_seen=datetime.now(UTC))
                else:
                    session.status = LiveTrackStatus.REJECTED_USER
                    session.rejected_reason = "rejected_user"
                    return False
        filt = self.options.get(CONF_ACTIVITY_FILTER, "all")
        if filt != "all" and (session.activity_type or "other").lower() != filt:
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
        try:
            await self.hass.services.async_call(domain, service, {"message": f"LiveTrack started: {session.garmin_user or 'Unknown'} ({session.activity_type or 'unknown'})"}, blocking=False)
            session.notification_started_sent = True
        except Exception:
            self.last_error = "notification_error"

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