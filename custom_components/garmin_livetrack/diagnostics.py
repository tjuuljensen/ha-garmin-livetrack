from .models import stable_session_hash


async def async_get_config_entry_diagnostics(hass, entry):
    manager = entry.runtime_data.manager
    options = getattr(manager, "options", {}) or {}
    sessions = getattr(manager, "sessions", {}) or {}
    ended_sessions = getattr(manager, "ended_sessions", {}) or {}
    known_users = getattr(manager, "known_users", {}) or {}
    return {
        "options": {
            **options,
            "notify_service": "redacted" if options.get("notify_service") else None,
        },
        "active_session_count": len(sessions),
        "ended_session_count": len(ended_sessions),
        "startup_debug": getattr(manager, "startup_debug", {}),
        "service_shape_change": {
            "suspected": getattr(manager, "shape_change_suspected", False),
            "consecutive_anomaly_count": getattr(manager, "shape_change_count", 0),
        },
        "known_users": [
            {
                "name": p.name,
                "enabled": p.enabled,
                "mode": p.mode,
                "first_event_consumed": p.first_event_consumed,
                "first_seen": p.first_seen.isoformat() if p.first_seen else None,
                "last_seen": p.last_seen.isoformat() if p.last_seen else None,
            }
            for p in known_users.values()
        ],
        "sessions": [
            {
                "session_id_hash": stable_session_hash(c.session.identity.session_id),
                "redacted_url": c.session.identity.redacted_url,
                "garmin_user": c.session.garmin_user,
                "activity": c.session.activity_type,
                "status": c.session.status.value,
                "trackpoint_count": c.session.trackpoint_count,
                "error_codes": [e.code for e in c.session.errors],
            }
            for c in sessions.values()
        ],
    }
