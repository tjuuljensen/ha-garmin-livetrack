import pytest

from custom_components.garmin_livetrack.diagnostics import async_get_config_entry_diagnostics


@pytest.mark.asyncio
async def test_diagnostics_redacts_notify(hass):
    class Entry: pass
    class Runtime: pass
    entry = Entry()
    runtime = Runtime()
    runtime.manager = type("M", (), {"options": {"notify_service": "notify.mobile"}, "sessions": {}, "ended_sessions": {}, "known_users": {}})()
    entry.runtime_data = runtime
    data = await async_get_config_entry_diagnostics(hass, entry)
    assert data["options"]["notify_service"] == "redacted"