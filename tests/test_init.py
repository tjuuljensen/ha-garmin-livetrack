from custom_components.garmin_livetrack.__init__ import _sanitize_entry_payload


def test_sanitize_entry_payload_removes_legacy_notification_keys():
    data = {
        "listen_to_imap_events": True,
        "enable_notifications": True,
        "notify_service": "notify.notify",
        "notification_start_template": "Start {user}",
    }
    options = {
        "ios_notification_style": True,
        "user_policies": {
            "Runner": {
                "name": "Runner",
                "enabled": True,
                "enable_notifications": False,
                "notify_service": "notify.mobile_app_phone",
                "ios_notification_style": True,
                "allowed_activities": ["running"],
            }
        },
    }

    clean_data, clean_options, changed = _sanitize_entry_payload(data, options)

    assert changed is True
    assert "enable_notifications" not in clean_data
    assert "notify_service" not in clean_data
    assert "notification_start_template" not in clean_data
    assert "ios_notification_style" not in clean_options
    assert clean_options["user_policies"]["Runner"] == {
        "name": "Runner",
        "enabled": True,
        "allowed_activities": ["running"],
    }
