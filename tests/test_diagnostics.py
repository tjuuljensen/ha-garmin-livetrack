import pytest

from custom_components.garmin_livetrack.diagnostics import async_get_config_entry_diagnostics


@pytest.mark.asyncio
async def test_diagnostics_exposes_user_agent_and_shape_change(hass):
    class Entry: pass
    class Runtime: pass
    entry = Entry()
    runtime = Runtime()
    runtime.manager = type(
        "M",
        (),
        {
            "options": {"user_agent": "CustomUA/2.0"},
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
    assert data["options"]["user_agent"] == "CustomUA/2.0"
    assert data["effective_user_agent"] == "CustomUA/2.0"
    assert data["service_shape_change"]["suspected"] is True
    assert data["service_shape_change"]["consecutive_anomaly_count"] == 4
    assert data["service_shape_change"]["issue_expected"] is True



@pytest.mark.asyncio
async def test_diagnostics_exposes_backoff_state_without_token(hass):
    class Entry: pass
    class Runtime: pass
    class Coord: pass
    class Session: pass
    class Identity: pass

    entry = Entry()
    runtime = Runtime()
    coord = Coord()
    session = Session()
    identity = Identity()
    identity.session_id = "session-1"
    identity.redacted_url = "https://livetrack.garmin.com/session/session-1/token/sec...ret"
    session.identity = identity
    session.garmin_user = "Runner"
    session.activity_type = "running"
    session.activity_type_raw = "Trail Running"
    session.status = type("Status", (), {"value": "active"})()
    session.trackpoint_count = 12
    session.errors = []
    coord.session = session
    coord.post_trackpoint_frequency_s = 10
    coord.last_trackpoint_fetch = None
    coord.next_trackpoints_allowed_at = None
    coord.backoff_until = None
    coord.consecutive_http_failures = 2
    coord.last_http_status = 429

    runtime.manager = type(
        "M",
        (),
        {
            "options": {"user_agent": "CustomUA/2.0"},
            "sessions": {"session-1": coord},
            "ended_sessions": {},
            "known_users": {},
            "shape_change_suspected": False,
            "shape_change_count": 0,
            "startup_debug": {},
            "_activity_policy_mode": lambda self, _name: "inherit_global",
            "_effective_activity_filter": lambda self, _name: "all",
            "_effective_user_agent": lambda self: "CustomUA/2.0",
        },
    )()
    entry.runtime_data = runtime

    data = await async_get_config_entry_diagnostics(hass, entry)

    session_row = data["sessions"][0]
    assert session_row["redacted_url"].endswith("sec...ret")
    assert "token" not in session_row
    assert session_row["consecutive_http_failures"] == 2
    assert session_row["last_http_status"] == 429
