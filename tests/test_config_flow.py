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


def test_normalize_keeps_profile_and_drops_advanced_flag():
    out = _normalize(
        {
            "allowed_users": "",
            "update_profile": "adaptive",
            "configure_advanced": True,
        },
        include_users=True,
    )
    assert out["update_profile"] == "adaptive"
    assert "configure_advanced" not in out


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
            "configure_advanced": False,
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
            "update_profile": "conservative",
            "configure_advanced": True,
        }
    )

    assert first["type"] == "form"
    assert first["step_id"] == "advanced"

    result = await flow.async_step_advanced(
        {
            "expose_debug_attributes": False,
        }
    )

    assert result["type"] == "create_entry"
    assert result["data"]["user_agent"] == DEFAULT_USER_AGENT
