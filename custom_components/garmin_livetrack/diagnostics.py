from .models import stable_session_hash


async def async_get_config_entry_diagnostics(hass, entry):
    manager = entry.runtime_data.manager
    return {
        "options": {
            **manager.options,
            "notify_service": "redacted" if manager.options.get("notify_service") else None,
        },
        "active_session_count": len(manager.sessions),
        "ended_session_count": len(manager.ended_sessions),
        "startup_debug": manager.startup_debug,
        "service_shape_change": {
            "suspected": manager.shape_change_suspected,
            "consecutive_anomaly_count": manager.shape_change_count,
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
            for p in manager.known_users.values()
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
            for c in manager.sessions.values()
        ],
    }
