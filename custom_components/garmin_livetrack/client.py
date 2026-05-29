from __future__ import annotations

import json
import html
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
NEXT_PUSH_RE = re.compile(r"self\.__next_f\.push\((.*?)\)</script>", re.DOTALL)


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
        if session is not None:
            self.session = session
        elif hass is not None:
            self.session = aiohttp_client.async_get_clientsession(hass)
        else:
            # Allow parse/normalize-only usage in unit tests without requiring a
            # Home Assistant instance.
            self.session = None
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
        if self.session is None:
            raise RuntimeError("HTTP session is not configured")
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
        result = GarminFetchResult(ok=False, source={"trackpoints_source": "none", "session_source": "none"})
        session_obj = self._find_session(session_payload, session_id)
        points = self._find_trackpoints(session_payload)
        if session_obj:
            result.source["session_source"] = "api_or_payload"
        if points:
            result.source["trackpoints_source"] = "api_or_payload"
        if not points and page_html:
            points = self._extract_next_data_points(page_html)
            if points:
                result.source["trackpoints_source"] = "hydration"
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
        for item in self._walk_values(payload):
            if self._looks_like_session(item, session_id):
                return dict(item)
        return {}

    def _find_trackpoints(self, payload: Any) -> list[dict[str, Any]]:
        candidates: list[list[dict[str, Any]]] = []
        for item in self._walk_values(payload):
            if self._looks_like_trackpoint_list(item):
                points = [point for point in item if isinstance(point, dict)]
                candidates.append(points)
            elif isinstance(item, dict):
                for key in ("trackPoints", "trackpoints", "points"):
                    nested = item.get(key)
                    if self._looks_like_trackpoint_list(nested):
                        candidates.append([point for point in nested if isinstance(point, dict)])
        if not candidates:
            return []
        return max(candidates, key=len)

    def _extract_next_data_points(self, page_html: str) -> list[dict[str, Any]]:
        for obj in self._extract_hydration_data(page_html):
            points = self._find_trackpoints(obj)
            if points:
                return points
        return []

    def _extract_next_push_points(self, page_html: str) -> list[dict[str, Any]]:
        for chunk in NEXT_PUSH_RE.findall(page_html):
            decoded_chunk = html.unescape(chunk)
            for string_match in re.finditer(r'"((?:\\.|[^"\\])*)"', decoded_chunk):
                try:
                    decoded = json.loads(f'"{string_match.group(1)}"')
                except json.JSONDecodeError:
                    continue
                if "trackPoints" in decoded or "sessionId" in decoded:
                    for arr in self._extract_json_arrays_by_key(decoded, "trackPoints"):
                        points = self._find_trackpoints(arr)
                        if points:
                            return points
                    parsed = self._safe_json_load(decoded)
                    if parsed is not None:
                        points = self._find_trackpoints(parsed)
                        if points:
                            return points
        return []

    def _extract_hydration_data(self, page_html: str) -> list[Any]:
        objects = self._load_next_data(page_html)
        objects.extend(self._extract_json_arrays_by_key(page_html, "trackPoints"))
        push_points = self._extract_next_push_points(page_html)
        if push_points:
            objects.append({"trackPoints": push_points})
        return objects

    def _load_next_data(self, page_html: str) -> list[Any]:
        objects: list[Any] = []
        for match in NEXT_DATA_RE.finditer(page_html):
            text = html.unescape(match.group(1)).strip()
            if not text:
                continue
            parsed = self._safe_json_load(text)
            if parsed is not None:
                objects.append(parsed)
        return objects

    def _extract_json_arrays_by_key(self, text: str, key: str) -> list[Any]:
        values: list[Any] = []
        marker = f'"{key}"'
        for marker_match in re.finditer(re.escape(marker), text):
            colon = text.find(":", marker_match.end())
            if colon == -1:
                continue
            start = text.find("[", colon)
            if start == -1:
                continue
            raw_array = self._balanced_json(text, start)
            if not raw_array:
                continue
            parsed = self._safe_json_load(html.unescape(raw_array))
            if parsed is not None:
                values.append(parsed)
        return values

    def _balanced_json(self, text: str, start: int) -> str | None:
        opening = text[start]
        closing = "]" if opening == "[" else "}"
        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == opening:
                depth += 1
            elif char == closing:
                depth -= 1
                if depth == 0:
                    return text[start : index + 1]
        return None

    def _walk_values(self, value: Any):
        yield value
        if isinstance(value, dict):
            for child in value.values():
                yield from self._walk_values(child)
        elif isinstance(value, list):
            for child in value:
                yield from self._walk_values(child)

    def _looks_like_trackpoint(self, value: Any) -> bool:
        return isinstance(value, dict) and isinstance(value.get("dateTime"), str) and (isinstance(value.get("position"), dict) or isinstance(value.get("fitnessPointData"), dict))

    def _looks_like_trackpoint_list(self, value: Any) -> bool:
        return isinstance(value, list) and any(self._looks_like_trackpoint(item) for item in value)

    def _looks_like_session(self, value: Any, session_id: str | None) -> bool:
        if not isinstance(value, dict):
            return False
        if session_id and value.get("sessionId") == session_id:
            return True
        return bool(value.get("sessionId") and (value.get("start") or value.get("userDisplayName")))

    def _safe_json_load(self, value: str) -> Any | None:
        try:
            return json.loads(value)
        except Exception:
            return None
