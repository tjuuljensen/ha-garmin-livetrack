import pytest

from custom_components.garmin_livetrack.diagnostics import async_get_config_entry_diagnostics


@pytest.mark.asyncio
async def test_diagnostics_redacts_notify(hass):
    class Entry: pass
    class Runtime: pass
    entry = Entry()
    runtime = Runtime()
    runtime.manager = type(
        "M",
        (),
        {
            "options": {"notify_service": "notify.mobile", "user_agent": "CustomUA/2.0"},
            "sessions": {},
            "ended_sessions": {},
            "known_users": {},
            "shape_change_suspected": True,
            "shape_change_count": 4,
            "_effective_user_agent": lambda self: "CustomUA/2.0",
        },
    )()
    entry.runtime_data = runtime
    data = await async_get_config_entry_diagnostics(hass, entry)
    assert data["options"]["notify_service"] == "redacted"
    assert data["effective_user_agent"] == "CustomUA/2.0"
    assert data["service_shape_change"]["suspected"] is True
    assert data["service_shape_change"]["consecutive_anomaly_count"] == 4
    assert data["service_shape_change"]["issue_expected"] is True
