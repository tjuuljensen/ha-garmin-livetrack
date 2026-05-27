from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

import aiohttp
from homeassistant.helpers import aiohttp_client

from .models import LiveTrackError, LiveTrackIdentity, LiveTrackSource, redact_url

SESSION_PATH_RE = re.compile(r"^/session/([^/\?]+)/token/([^/\?]+)")
SESSION_QUERY_RE = re.compile(r"^/session/([^/\?]+)$")
CSRF_META_RE = re.compile(r"<meta[^>]+(?:name|property)=[\"'](?:csrf-token|livetrack-csrf-token)[\"'][^>]+content=[\"']([^\"']+)", re.IGNORECASE)
NEXT_DATA_RE = re.compile(r"<script id=\"__NEXT_DATA__\" type=\"application/json\">(.*?)</script>", re.DOTALL)


@dataclass
class GarminFetchResult:
    ok: bool
    session: dict[str, Any] = field(default_factory=dict)
    trackpoints: list[dict[str, Any]] = field(default_factory=list)
    last_trackpoint: dict[str, Any] = field(default_factory=dict)
    trackpoint_count: int = 0
    errors: list[LiveTrackError] = field(default_factory=list)
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    source: dict[str, Any] = field(default_factory=dict)
    page_status: int | None = None
    api_status: int | None = None
    csrf_found: bool = False


class GarminLiveTrackClient:
    def __init__(self, hass, session: aiohttp.ClientSession | None = None, request_timeout: int = 20) -> None:
        self.hass = hass
        self.session = session or aiohttp_client.async_get_clientsession(hass)
        self.request_timeout = request_timeout

    def parse_livetrack_identity(self, url: str | None, session_id: str | None = None, token: str | None = None, source: LiveTrackSource = LiveTrackSource.MANUAL) -> LiveTrackIdentity:
        raw = (url or "").replace("=\r\n", "").replace("=\n", "").strip()
        if raw:
            parsed = urlparse(raw)
            if parsed.hostname != "livetrack.garmin.com":
                raise ValueError("invalid_url")
            m = SESSION_PATH_RE.match(parsed.path)
            if m:
                session_id, token = m.group(1), m.group(2)
            else:
                q = SESSION_QUERY_RE.match(parsed.path)
                if q:
                    session_id = q.group(1)
                    token = parse_qs(parsed.query).get("token", [None])[0]
        if not session_id:
            raise ValueError("missing_session_id")
        if not token:
            raise ValueError("missing_token")
        canonical = f"https://livetrack.garmin.com/session/{session_id}/token/{token}"
        return LiveTrackIdentity(session_id=session_id, token=token, canonical_url=canonical, redacted_url=redact_url(canonical), source=source)

    async def fetch(self, identity: LiveTrackIdentity) -> GarminFetchResult:
        result = GarminFetchResult(ok=False, source={"session_id": identity.session_id})
        page_html = ""
        csrf = None

        try:
            async with self.session.get(identity.canonical_url, timeout=self.request_timeout, headers={"User-Agent": "HomeAssistant-GarminLiveTrack/0.1.0"}) as resp:
                result.page_status = resp.status
                if resp.status >= 400:
                    result.errors.append(LiveTrackError("page_http_error", f"HTTP {resp.status}", datetime.now(UTC), True))
                    return result
                page_html = await resp.text()
                m = CSRF_META_RE.search(page_html)
                if m:
                    csrf = m.group(1)
                    result.csrf_found = True
        except aiohttp.InvalidURL:
            result.errors.append(LiveTrackError("page_url_error", "Invalid page URL", datetime.now(UTC), False))
            return result
        except aiohttp.ClientError as err:
            result.errors.append(LiveTrackError("page_request_error", type(err).__name__, datetime.now(UTC), True))
            return result

        headers = {
            "User-Agent": "HomeAssistant-GarminLiveTrack/0.1.0",
            "Accept": "application/json, text/plain, */*",
            "Referer": identity.canonical_url,
        }
        if csrf:
            headers["Livetrack-Csrf-Token"] = csrf

        payload: Any = {}
        api_url = f"https://livetrack.garmin.com/api/sessions/{identity.session_id}?token={identity.token}"
        try:
            async with self.session.get(api_url, timeout=self.request_timeout, headers=headers) as resp:
                result.api_status = resp.status
                if resp.status >= 400:
                    result.errors.append(LiveTrackError("session_http_error", f"HTTP {resp.status}", datetime.now(UTC), True))
                else:
                    payload = await resp.json(content_type=None)
        except aiohttp.InvalidURL:
            result.errors.append(LiveTrackError("session_url_error", "Invalid session API URL", datetime.now(UTC), False))
        except aiohttp.ClientError as err:
            result.errors.append(LiveTrackError("session_request_error", type(err).__name__, datetime.now(UTC), True))
        except json.JSONDecodeError:
            result.errors.append(LiveTrackError("malformed_response", "Invalid JSON", datetime.now(UTC), True))

        normalized = self.normalize_payload(payload, page_html, identity.session_id)
        normalized.page_status = result.page_status
        normalized.api_status = result.api_status
        normalized.csrf_found = result.csrf_found
        normalized.errors = result.errors + normalized.errors
        return normalized

    def normalize_payload(self, session_payload: Any, page_html: str, session_id: str) -> GarminFetchResult:
        result = GarminFetchResult(ok=False)
        session_obj = self._find_session(session_payload, session_id)
        points = self._find_trackpoints(session_payload)
        if not points and page_html:
            points = self._extract_next_data_points(page_html)
        if not session_obj:
            result.errors.append(LiveTrackError("missing_session", "Could not find session", datetime.now(UTC), True))
        if not points:
            result.errors.append(LiveTrackError("missing_trackpoints", "No trackpoints", datetime.now(UTC), True))
        result.session = session_obj or {}
        result.trackpoints = points
        result.trackpoint_count = len(points)
        result.last_trackpoint = points[-1] if points else {}
        result.ok = bool(session_obj)
        return result

    def _find_session(self, payload: Any, session_id: str) -> dict[str, Any] | None:
        if isinstance(payload, dict):
            if str(payload.get("sessionId", "")) == session_id:
                return payload
            for value in payload.values():
                found = self._find_session(value, session_id)
                if found:
                    return found
        elif isinstance(payload, list):
            for item in payload:
                found = self._find_session(item, session_id)
                if found:
                    return found
        return None

    def _find_trackpoints(self, payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, dict):
            for key, value in payload.items():
                if key in {"trackPoints", "trackpoints", "points"} and isinstance(value, list):
                    return [p for p in value if isinstance(p, dict)]
                found = self._find_trackpoints(value)
                if found:
                    return found
        elif isinstance(payload, list):
            if payload and all(isinstance(p, dict) for p in payload) and any("dateTime" in p or "position" in p or "fitnessPointData" in p for p in payload):
                return payload
            for item in payload:
                found = self._find_trackpoints(item)
                if found:
                    return found
        return []

    def _extract_next_data_points(self, page_html: str) -> list[dict[str, Any]]:
        m = NEXT_DATA_RE.search(page_html)
        if not m:
            return []
        try:
            payload = json.loads(m.group(1))
        except json.JSONDecodeError:
            return []
        return self._find_trackpoints(payload)