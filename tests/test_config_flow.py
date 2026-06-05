import pytest

from custom_components.garmin_livetrack.config_flow import (
    GarminLiveTrackOptionsFlow,
    _normalize,
)
from custom_components.garmin_livetrack.const import DEFAULT_USER_AGENT


def test_normalize_allowed_users():
    out = _normalize({"allowed_users": "alice, bob"}, include_users=True)
    assert out["allowed_users"] == ["alice", "bob"]


def test_normalize_empty_user_agent_reverts_to_default():
    out = _normalize(
        {
            "allowed_users": "alice",
            "user_agent": "",
        },
        include_users=True,
    )
    assert out["user_agent"] == DEFAULT_USER_AGENT


@pytest.mark.asyncio
async def test_options_flow_missing_user_agent_reverts_to_default(hass):
    class _FakeConfigEntry:
        data = {}
        options = {
            "user_agent": "CustomUA/2.0",
        }

    flow = GarminLiveTrackOptionsFlow(_FakeConfigEntry())
    flow.hass = hass

    result = await flow.async_step_init(
        {
            "listen_to_imap_events": True,
            "strict_users": False,
            "accept_first_seen_users": False,
            "allowed_users": "",
            "activity_filter": "all",
            "update_profile": "conservative",
            "update_interval_seconds": 60,
            "initial_trackpoint_wait_minutes": 10,
            "max_runtime_hours": 12,
            "stale_minutes": 10,
            "finalization_minutes": 5,
            "retain_ended_hours": 6,
            "defer_startup_poll_seconds": 0,
            "expose_debug_attributes": False,
        }
    )

    assert result["type"] == "create_entry"
    assert result["data"]["user_agent"] == DEFAULT_USER_AGENT
