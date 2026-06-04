from custom_components.garmin_livetrack.config_flow import _normalize

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
