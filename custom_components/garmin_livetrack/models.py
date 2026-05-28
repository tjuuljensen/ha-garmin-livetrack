from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any
from urllib.parse import parse_qs, urlparse


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
    notification_started_sent: bool = False
    notification_ended_sent: bool = False


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
        return "other"
    low = value.strip().lower()
    if "run" in low:
        return "running"
    if "walk" in low or "hike" in low:
        return "walking"
    if "cycl" in low or "bike" in low:
        return "cycling"
    if "strength" in low or "gym" in low or "weight" in low:
        return "strength"
    if "swim" in low:
        return "swimming"
    aliases = {
        "run": "running",
        "running": "running",
        "walk": "walking",
        "walking": "walking",
        "bike": "cycling",
        "cycling": "cycling",
        "strength": "strength",
        "swim": "swimming",
        "swimming": "swimming",
    }
    return aliases.get(low, "other")


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
