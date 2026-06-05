from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any
from urllib.parse import parse_qs, urlparse

DEFAULT_ACTIVITY = "other"

ACTIVITY_ALIASES = {
    "run": "running",
    "running": "running",
    "trail_running": "running",
    "bike": "cycling",
    "biking": "cycling",
    "cycling": "cycling",
    "road_biking": "cycling",
    "mountain_biking": "cycling",
    "indoor_cycling": "cycling",
    "walk": "walking",
    "walking": "walking",
    "hike": "walking",
    "hiking": "walking",
    "strength": "strength",
    "strength_training": "strength",
    "gym": "strength",
    "swim": "swimming",
    "swimming": "swimming",
    "open_water_swimming": "swimming",
    "pool_swimming": "swimming",
    "kayak": "kayak",
    "kayaking": "kayak",
    "canoe": "kayak",
    "canoeing": "kayak",
    "paddle_sports": "kayak",
    "rowing": "rowing",
    "indoor_rowing": "rowing",
}


class LiveTrackStatus(StrEnum):
    DISCOVERED = "discovered"
    FETCHING = "fetching"
    WAITING_FOR_TRACKPOINT = "waiting_for_trackpoint"
    ACTIVE = "active"
    ENDING = "ending"
    ENDED = "ended"
    EXPIRED = "expired"
    STOPPED = "stopped"
    REJECTED_USER = "rejected_user"
    REJECTED_ACTIVITY = "rejected_activity"
    INVALID_URL = "invalid_url"
    GARMIN_ERROR = "garmin_error"
    STALE = "stale"
    DUPLICATE = "duplicate"


class LiveTrackSource(StrEnum):
    IMAP = "imap"
    MANUAL = "manual"
    SERVICE = "service"
    RECOVERY = "recovery"


@dataclass
class LiveTrackIdentity:
    session_id: str
    token: str
    canonical_url: str
    redacted_url: str
    source: LiveTrackSource


@dataclass
class LiveTrackError:
    code: str
    message: str
    timestamp: datetime
    retryable: bool


@dataclass
class LiveTrackPoint:
    timestamp: datetime | None = None
    latitude: float | None = None
    longitude: float | None = None
    altitude_m: float | None = None
    speed_mps: float | None = None
    distance_m: float | None = None
    duration_s: float | None = None
    heart_rate_bpm: int | None = None
    power_w: int | None = None
    cadence: float | None = None
    event_types: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class LiveTrackSession:
    identity: LiveTrackIdentity
    garmin_user: str | None
    activity_type: str | None
    start: datetime | None
    expected_end: datetime | None
    actual_end: datetime | None
    first_seen: datetime
    last_fetch: datetime | None
    last_success: datetime | None
    last_point: LiveTrackPoint | None
    trackpoint_count: int
    status: LiveTrackStatus
    errors: list[LiveTrackError] = field(default_factory=list)
    rejected_reason: str | None = None
    end_reason: str | None = None
    activity_type_raw: str | None = None


def redact_token(value: str) -> str:
    if not value:
        return ""
    return f"{value[:3]}...{value[-3:]}" if len(value) > 8 else "***"


def redact_url(url: str) -> str:
    parsed = urlparse(url)
    token = parse_qs(parsed.query).get("token", [None])[0]
    if "/token/" in parsed.path:
        base, _, tok = parsed.path.partition("/token/")
        return f"{parsed.scheme}://{parsed.netloc}{base}/token/{redact_token(tok)}"
    if token:
        return url.replace(token, redact_token(token))
    return url


def stable_session_hash(session_id: str) -> str:
    return sha256(session_id.encode("utf-8")).hexdigest()[:12]


def normalize_activity(value: str | None) -> str:
    if not value:
        return DEFAULT_ACTIVITY
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    return ACTIVITY_ALIASES.get(normalized, normalized)


def speed_kmh_from_mps(speed_mps: float | None) -> float | None:
    if speed_mps is None:
        return None
    return round(float(speed_mps) * 3.6, 2)


def pace_min_km_from_speed_mps(speed_mps: float | None) -> float | None:
    if speed_mps is None or float(speed_mps) <= 0:
        return None
    return round(16.666666667 / float(speed_mps), 2)


def distance_km_from_m(distance_m: float | None) -> float | None:
    if distance_m is None:
        return None
    return round(float(distance_m) / 1000.0, 3)


def duration_hms_from_seconds(duration_s: float | None) -> str | None:
    if duration_s is None:
        return None
    total = max(0, int(round(float(duration_s))))
    hours = total // 3600
    minutes = (total % 3600) // 60
    seconds = total % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def has_location(point: LiveTrackPoint | None) -> bool:
    return bool(point and point.latitude is not None and point.longitude is not None)


def parse_garmin_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=UTC)
        except Exception:
            return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            return None
    return None


def extract_event_types(point: dict[str, Any]) -> list[str]:
    values = point.get("eventTypes") or point.get("event_types") or []
    if isinstance(values, list):
        return [str(v) for v in values]
    return []
