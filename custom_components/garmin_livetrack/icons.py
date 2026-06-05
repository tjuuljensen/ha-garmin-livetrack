from __future__ import annotations

from .models import DEFAULT_ACTIVITY, normalize_activity

DEFAULT_ICON = "mdi:map-marker-path"

ACTIVITY_ICONS = {
    "running": {
        "active": "mdi:run-fast",
        "inactive": "mdi:run",
    },
    "cycling": {
        "active": "mdi:bike-fast",
        "inactive": "mdi:bike",
    },
    "walking": {
        "active": "mdi:walk",
        "inactive": "mdi:walk",
    },
    "strength": {
        "active": "mdi:weight-lifter",
        "inactive": "mdi:weight-lifter",
    },
    "swimming": {
        "active": "mdi:swim",
        "inactive": "mdi:swim",
    },
    "kayak": {
        "active": "mdi:kayaking",
        "inactive": "mdi:kayaking",
    },
    "rowing": {
        "active": "mdi:rowing",
        "inactive": "mdi:rowing",
    },
    DEFAULT_ACTIVITY: {
        "active": DEFAULT_ICON,
        "inactive": DEFAULT_ICON,
    },
}

def activity_icon(activity: str | None, is_active: bool) -> str:
    icons = ACTIVITY_ICONS.get(normalize_activity(activity))
    if not icons:
        return DEFAULT_ICON
    return icons["active" if is_active else "inactive"]
