import pytest

from custom_components.garmin_livetrack.config_flow import (
    GarminLiveTrackOptionsFlow,
    _normalize,
)
from custom_components.garmin_livetrack.const import DEFAULT_USER_AGENT

def test_normalize_allowed_users():
    out=_normalize({'allowed_users':'alice, bob','notify_service':'notify.notify'}, include_users=True)
    assert out['allowed_users']==['alice','bob']


def test_normalize_notification_templates():
    out = _normalize(
        {
            'allowed_users': 'alice',
            'notify_service': 'notify.notify',
            'user_agent': 'MyAgent/1.0',
            'notification_start_template': 'Start {user} {activity}',
            'notification_end_template': 'End {user} {reason}',
        },
        include_users=True,
    )
    assert out['user_agent'] == 'MyAgent/1.0'
    assert out['notification_start_template'] == 'Start {user} {activity}'
    assert out['notification_end_template'] == 'End {user} {reason}'


def test_normalize_empty_user_agent_reverts_to_default():
    out = _normalize(
        {
            'allowed_users': 'alice',
            'notify_service': 'notify.notify',
            'user_agent': '',
        },
        include_users=True,
    )
    assert out['user_agent'] == DEFAULT_USER_AGENT


@pytest.mark.asyncio
async def test_options_flow_missing_user_agent_reverts_to_default(hass):
    class _FakeConfigEntry:
        data = {}
        options = {
            'user_agent': 'CustomUA/2.0',
            'notify_service': 'notify.notify',
        }

    hass.services.async_register('notify', 'notify', lambda call: None)
    flow = GarminLiveTrackOptionsFlow(_FakeConfigEntry())
    flow.hass = hass

    result = await flow.async_step_init(
        {
            'listen_to_imap_events': True,
            'enable_notifications': False,
            'notify_service': 'notify.notify',
            'notification_start_template': 'LiveTrack started: {user} ({activity})',
            'notification_end_template': 'LiveTrack ended: {user} ({activity}) - {reason}',
            'ios_notification_style': True,
            'strict_users': False,
            'accept_first_seen_users': False,
            'allowed_users': '',
            'activity_filter': 'all',
            'update_interval_seconds': 60,
            'initial_trackpoint_wait_minutes': 10,
            'max_runtime_hours': 12,
            'stale_minutes': 10,
            'finalization_minutes': 5,
            'retain_ended_hours': 6,
            'defer_startup_poll_seconds': 0,
            'expose_debug_attributes': False,
        }
    )

    assert result['type'] == 'create_entry'
    assert result['data']['user_agent'] == DEFAULT_USER_AGENT
