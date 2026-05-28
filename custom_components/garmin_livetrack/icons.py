from __future__ import annotations


def activity_icon(activity: str | None, is_active: bool) -> str:
    value = (activity or "other").strip().lower()
    if value == "running":
        return "mdi:run-fast" if is_active else "mdi:run"
    if value == "cycling":
        return "mdi:bike-fast" if is_active else "mdi:bike"
    if value == "walking":
        return "mdi:walk"
    if value == "strength":
        return "mdi:weight-lifter"
    if value == "swimming":
        return "mdi:swim"
    return "mdi:map-marker-path"
