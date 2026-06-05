import pytest

from custom_components.garmin_livetrack.config_flow import GarminLiveTrackOptionsFlow, _normalize
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


def test_normalize_keeps_profile():
    out = _normalize(
        {
            "allowed_users": "",
            "update_profile": "adaptive",
        },
        include_users=True,
    )
    assert out["update_profile"] == "adaptive"


@pytest.mark.asyncio
async def test_options_flow_without_advanced_preserves_existing_user_agent(hass):
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
        }
    )

    assert result["type"] == "create_entry"
    assert result["data"]["user_agent"] == "CustomUA/2.0"


@pytest.mark.asyncio
async def test_advanced_step_missing_user_agent_reverts_to_default(hass):
    class _FakeConfigEntry:
        data = {}
        options = {
            "user_agent": "CustomUA/2.0",
        }

    flow = GarminLiveTrackOptionsFlow(_FakeConfigEntry())
    flow.hass = hass

    first = await flow.async_step_init(
        {
            "listen_to_imap_events": True,
            "strict_users": False,
            "accept_first_seen_users": False,
            "allowed_users": "",
            "activity_filter": "all",
            "update_profile": "custom",
        }
    )

    assert first["type"] == "form"
    assert first["step_id"] == "advanced_profile"

    second = await flow.async_step_advanced_profile(
        {
            "advanced_profile_defaults": "conservative",
        }
    )

    assert second["type"] == "form"
    assert second["step_id"] == "advanced"

    result = await flow.async_step_advanced(
        {
            "expose_debug_attributes": False,
            "update_interval_seconds": 60,
            "use_garmin_trackpoint_frequency": False,
            "initial_trackpoint_wait_minutes": 10,
            "max_runtime_hours": 12,
            "stale_minutes": 10,
            "finalization_minutes": 5,
            "retain_ended_hours": 6,
            "defer_startup_poll_seconds": 0,
        }
    )

    assert result["type"] == "create_entry"
    assert result["data"]["user_agent"] == DEFAULT_USER_AGENT


@pytest.mark.asyncio
async def test_advanced_profile_step_defaults_to_existing_when_advanced_settings_exist(hass):
    class _FakeConfigEntry:
        data = {}
        options = {
            "update_profile": "custom",
            "advanced_profile_defaults": "adaptive",
            "update_interval_seconds": 42,
            "use_garmin_trackpoint_frequency": True,
        }

    flow = GarminLiveTrackOptionsFlow(_FakeConfigEntry())
    flow.hass = hass
    flow._pending_options = {"update_profile": "custom", **_FakeConfigEntry.options}

    result = await flow.async_step_advanced_profile()

    assert result["type"] == "form"
    assert result["step_id"] == "advanced_profile"


@pytest.mark.asyncio
async def test_options_flow_edit_user_takes_priority_over_advanced_profile(hass):
    class _FakeConfigEntry:
        data = {}
        options = {
            "update_profile": "custom",
            "allowed_users": ["Runner"],
            "user_policies": {
                "Runner": {
                    "name": "Runner",
                    "enabled": True,
                    "mode": "normal",
                }
            },
        }

    flow = GarminLiveTrackOptionsFlow(_FakeConfigEntry())
    flow.hass = hass

    result = await flow.async_step_init(
        {
            "listen_to_imap_events": True,
            "strict_users": False,
            "accept_first_seen_users": False,
            "allowed_users": "Runner",
            "activity_filter": "all",
            "update_profile": "custom",
            "edit_user": "Runner",
        }
    )

    assert result["type"] == "form"
    assert result["step_id"] == "user_policy"
