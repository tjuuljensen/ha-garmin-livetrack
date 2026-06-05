from __future__ import annotations

import json
import html
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import aiohttp
from homeassistant.helpers import aiohttp_client

from .models import LiveTrackError, LiveTrackIdentity, LiveTrackSource, redact_url
from .const import DEFAULT_USER_AGENT

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


@dataclass
class GarminTrackpointResult:
    ok: bool
    trackpoints: list[dict[str, Any]] = field(default_factory=list)
    errors: list[LiveTrackError] = field(default_factory=list)
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    source: dict[str, Any] = field(default_factory=dict)
    page_status: int | None = None
    api_status: int | None = None
    csrf_found: bool = False


@dataclass
class _PageContext:
    html: str = ""
    csrf: str | None = None
    csrf_found: bool = False
    page_status: int | None = None
    errors: list[LiveTrackError] = field(default_factory=list)


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
        self.user_agent = DEFAULT_USER_AGENT

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
        return await self.fetch_full(identity)

    def _session_api_url(self, identity: LiveTrackIdentity) -> str:
        return f"https://livetrack.garmin.com/api/sessions/{identity.session_id}?token={identity.token}"

    def _coerce_begin(self, begin: datetime | str | None) -> str | None:
        if begin is None:
            return None
        if isinstance(begin, datetime):
            normalized = begin.astimezone(UTC).isoformat()
            return normalized.replace("+00:00", "Z")
        text = str(begin).strip()
        return text or None

    def _trackpoints_api_url(self, identity: LiveTrackIdentity, begin: datetime | str | None = None) -> str:
        params = {"token": identity.token}
        begin_value = self._coerce_begin(begin)
        if begin_value:
            params["begin"] = begin_value
        return f"https://livetrack.garmin.com/api/sessions/{identity.session_id}/track-points/common?{urlencode(params)}"

    def _api_headers(self, identity: LiveTrackIdentity, csrf: str | None) -> dict[str, str]:
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/json, text/plain, */*",
            "Referer": identity.canonical_url,
        }
        if csrf:
            headers["Livetrack-Csrf-Token"] = csrf
        return headers

    async def _fetch_page_context(self, identity: LiveTrackIdentity) -> _PageContext:
        if self.session is None:
            raise RuntimeError("HTTP session is not configured")
        context = _PageContext()
        try:
            async with self.session.get(identity.canonical_url, timeout=self.request_timeout, headers={"User-Agent": self.user_agent}) as resp:
                context.page_status = resp.status
                if resp.status >= 400:
                    context.errors.append(LiveTrackError("page_http_error", f"HTTP {resp.status}", datetime.now(UTC), True))
                    return context
                context.html = await resp.text()
                m = CSRF_META_RE.search(context.html)
                if m:
                    context.csrf = m.group(1)
                    context.csrf_found = True
        except aiohttp.InvalidURL:
            context.errors.append(LiveTrackError("page_url_error", "Invalid page URL", datetime.now(UTC), False))
            return context
        except aiohttp.ClientError as err:
            context.errors.append(LiveTrackError("page_request_error", type(err).__name__, datetime.now(UTC), True))
            return context
        return context

    async def _fetch_with_csrf_retry(
        self,
        identity: LiveTrackIdentity,
        url: str,
        error_prefix: str,
        page_context: _PageContext | None = None,
    ) -> tuple[Any, _PageContext, int | None, list[LiveTrackError]]:
        context = page_context or await self._fetch_page_context(identity)
        errors = list(context.errors)
        if context.errors:
            return {}, context, None, errors

        for attempt in range(2):
            try:
                async with self.session.get(
                    url,
                    timeout=self.request_timeout,
                    headers=self._api_headers(identity, context.csrf),
                ) as resp:
                    status = resp.status
                    if status == 403 and attempt == 0:
                        context = await self._fetch_page_context(identity)
                        errors.extend(context.errors)
                        if context.errors:
                            return {}, context, status, errors
                        continue
                    if status >= 400:
                        errors.append(LiveTrackError(f"{error_prefix}_http_error", f"HTTP {status}", datetime.now(UTC), True))
                        return {}, context, status, errors
                    return await resp.json(content_type=None), context, status, errors
            except aiohttp.InvalidURL:
                errors.append(LiveTrackError(f"{error_prefix}_url_error", "Invalid session API URL", datetime.now(UTC), False))
                return {}, context, None, errors
            except aiohttp.ClientError as err:
                errors.append(LiveTrackError(f"{error_prefix}_request_error", type(err).__name__, datetime.now(UTC), True))
                return {}, context, None, errors
            except json.JSONDecodeError:
                errors.append(LiveTrackError("malformed_response", "Invalid JSON", datetime.now(UTC), True))
                return {}, context, status, errors
        return {}, context, None, errors

    async def fetch_session(self, identity: LiveTrackIdentity) -> GarminFetchResult:
        page_context = await self._fetch_page_context(identity)
        payload, context, api_status, errors = await self._fetch_with_csrf_retry(
            identity,
            self._session_api_url(identity),
            "session",
            page_context=page_context,
        )
        session_obj = self._find_session(payload, identity.session_id)
        result = GarminFetchResult(
            ok=bool(session_obj),
            session=session_obj or {},
            errors=errors,
            source={"session_source": "api_or_payload" if session_obj else "none", "trackpoints_source": "none"},
            page_status=context.page_status,
            api_status=api_status,
            csrf_found=context.csrf_found,
        )
        if not session_obj:
            result.errors.append(LiveTrackError("missing_session", "Could not find session", datetime.now(UTC), True))
        return result

    async def fetch_trackpoints(
        self,
        identity: LiveTrackIdentity,
        begin: datetime | str | None = None,
    ) -> GarminTrackpointResult:
        page_context = await self._fetch_page_context(identity)
        payload, context, api_status, errors = await self._fetch_with_csrf_retry(
            identity,
            self._trackpoints_api_url(identity, begin),
            "trackpoints",
            page_context=page_context,
        )
        points = self._find_trackpoints(payload)
        result = GarminTrackpointResult(
            ok=bool(points),
            trackpoints=points,
            errors=errors,
            source={"trackpoints_source": "trackpoints_common" if points else "none"},
            page_status=context.page_status,
            api_status=api_status,
            csrf_found=context.csrf_found,
        )
        if not points:
            result.errors.append(LiveTrackError("missing_trackpoints", "No trackpoints", datetime.now(UTC), True))
        return result

    async def fetch_legacy_full(self, identity: LiveTrackIdentity) -> GarminFetchResult:
        page_context = await self._fetch_page_context(identity)
        if page_context.errors:
            return GarminFetchResult(
                ok=False,
                errors=list(page_context.errors),
                source={"session_id": identity.session_id},
                page_status=page_context.page_status,
                csrf_found=page_context.csrf_found,
            )
        session_payload, page_context, session_status, session_errors = await self._fetch_with_csrf_retry(
            identity,
            self._session_api_url(identity),
            "session",
            page_context=page_context,
        )
        normalized = self.normalize_payload(session_payload, page_context.html, identity.session_id)
        normalized.page_status = page_context.page_status
        normalized.api_status = session_status
        normalized.csrf_found = page_context.csrf_found
        normalized.errors = session_errors + normalized.errors
        return normalized

    async def fetch_full(self, identity: LiveTrackIdentity) -> GarminFetchResult:
        page_context = await self._fetch_page_context(identity)
        if page_context.errors:
            return GarminFetchResult(
                ok=False,
                errors=list(page_context.errors),
                source={"session_id": identity.session_id},
                page_status=page_context.page_status,
                csrf_found=page_context.csrf_found,
            )

        session_payload, page_context, session_status, session_errors = await self._fetch_with_csrf_retry(
            identity,
            self._session_api_url(identity),
            "session",
            page_context=page_context,
        )
        trackpoint_payload, page_context, trackpoint_status, trackpoint_errors = await self._fetch_with_csrf_retry(
            identity,
            self._trackpoints_api_url(identity),
            "trackpoints",
            page_context=page_context,
        )
        session_obj = self._find_session(session_payload, identity.session_id)
        trackpoints = self._find_trackpoints(trackpoint_payload)
        if session_obj and trackpoints:
            return GarminFetchResult(
                ok=True,
                session=session_obj,
                trackpoints=trackpoints,
                last_trackpoint=trackpoints[-1],
                trackpoint_count=len(trackpoints),
                errors=session_errors + trackpoint_errors,
                source={
                    "session_source": "api_or_payload",
                    "trackpoints_source": "trackpoints_common",
                },
                page_status=page_context.page_status,
                api_status=trackpoint_status or session_status,
                csrf_found=page_context.csrf_found,
            )

        legacy = self.normalize_payload(session_payload, page_context.html, identity.session_id)
        legacy.page_status = page_context.page_status
        legacy.api_status = session_status
        legacy.csrf_found = page_context.csrf_found
        legacy.errors = session_errors + trackpoint_errors + legacy.errors
        return legacy

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
