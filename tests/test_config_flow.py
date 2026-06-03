from custom_components.garmin_livetrack.config_flow import _normalize

def test_normalize_allowed_users():
    out=_normalize({'allowed_users':'alice, bob','notify_service':'notify.notify'}, include_users=True)
    assert out['allowed_users']==['alice','bob']
