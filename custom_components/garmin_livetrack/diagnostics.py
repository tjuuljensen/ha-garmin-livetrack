from .models import stable_session_hash


async def async_get_config_entry_diagnostics(hass, entry):
    manager = entry.runtime_data.manager
    options = getattr(manager, "options", {}) or {}
    sessions = getattr(manager, "sessions", {}) or {}
    ended_sessions = getattr(manager, "ended_sessions", {}) or {}
    known_users = getattr(manager, "known_users", {}) or {}
    return {
        "options": options,
        "effective_user_agent": manager._effective_user_agent(),
        "active_session_count": len(sessions),
        "ended_session_count": len(ended_sessions),
        "startup_debug": getattr(manager, "startup_debug", {}),
        "service_shape_change": {
            "suspected": getattr(manager, "shape_change_suspected", False),
            "consecutive_anomaly_count": getattr(manager, "shape_change_count", 0),
            "issue_expected": bool(getattr(manager, "shape_change_suspected", False)),
        },
        "known_users": [
            {
                "name": p.name,
                "enabled": p.enabled,
                "mode": p.mode,
                "first_event_consumed": p.first_event_consumed,
                "first_seen": p.first_seen.isoformat() if p.first_seen else None,
                "last_seen": p.last_seen.isoformat() if p.last_seen else None,
                "allowed_activities": p.allowed_activities,
                "activity_policy_mode": manager._activity_policy_mode(p.name),
                "effective_activity_filter": manager._effective_activity_filter(p.name),
            }
            for p in known_users.values()
        ],
        "sessions": [
            {
                "session_id_hash": stable_session_hash(c.session.identity.session_id),
                "redacted_url": c.session.identity.redacted_url,
                "garmin_user": c.session.garmin_user,
                "activity": c.session.activity_type,
                "activity_type_raw": c.session.activity_type_raw,
                "status": c.session.status.value,
                "trackpoint_count": c.session.trackpoint_count,
                "post_trackpoint_frequency_s": c.post_trackpoint_frequency_s,
                "last_trackpoint_fetch": c.last_trackpoint_fetch.isoformat() if c.last_trackpoint_fetch else None,
                "next_trackpoints_allowed_at": c.next_trackpoints_allowed_at.isoformat() if c.next_trackpoints_allowed_at else None,
                "backoff_until": c.backoff_until.isoformat() if c.backoff_until else None,
                "consecutive_http_failures": c.consecutive_http_failures,
                "last_http_status": c.last_http_status,
                "error_codes": [e.code for e in c.session.errors],
            }
            for c in sessions.values()
        ],
    }
