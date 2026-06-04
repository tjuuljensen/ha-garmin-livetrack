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
        "effective_user_agent": manager._effective_user_agent(),
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
                "enable_notifications": p.enable_notifications,
                "notify_service": "configured" if p.notify_service else None,
                "ios_notification_style": p.ios_notification_style,
                "notification_policy_mode": manager._notification_policy_mode(p.name),
                "notify_service_policy_mode": manager._notify_service_policy_mode(p.name),
                "ios_notification_style_policy_mode": manager._ios_notification_style_policy_mode(p.name),
                "allowed_activities": p.allowed_activities,
                "activity_policy_mode": manager._activity_policy_mode(p.name),
                "effective_enable_notifications": manager._effective_notifications_enabled(p.name),
                "effective_notify_service": "configured" if manager._effective_notify_service(p.name) else None,
                "effective_ios_notification_style": manager._effective_ios_notification_style(p.name),
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
                "status": c.session.status.value,
                "trackpoint_count": c.session.trackpoint_count,
                "error_codes": [e.code for e in c.session.errors],
            }
            for c in sessions.values()
        ],
    }
