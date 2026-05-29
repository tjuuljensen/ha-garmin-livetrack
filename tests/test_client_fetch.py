from custom_components.garmin_livetrack.client import GarminLiveTrackClient


def test_normalize_missing_trackpoints_no_crash():
    c = GarminLiveTrackClient(hass=None, session=None)
    r = c.normalize_payload({"sessionId": "abc"}, "", "abc")
    assert r.ok
    assert any(e.code == "missing_trackpoints" for e in r.errors)


def test_next_data_fallback():
    c = GarminLiveTrackClient(hass=None, session=None)
    html = '<script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"trackPoints":[{"dateTime":"2026-01-01T00:00:00Z","position":{"lat":55.67,"lon":12.56}}]}}}</script>'
    r = c.normalize_payload({"sessionId": "abc"}, html, "abc")
    assert r.trackpoint_count == 1
