from custom_components.garmin_livetrack.client import GarminLiveTrackClient


def test_parse_path_form():
    c = GarminLiveTrackClient(hass=None, session=None)
    i = c.parse_livetrack_identity("https://livetrack.garmin.com/session/abc/token/def")
    assert i.session_id == "abc"
    assert i.token == "def"


def test_parse_query_form():
    c = GarminLiveTrackClient(hass=None, session=None)
    i = c.parse_livetrack_identity("https://livetrack.garmin.com/session/abc?token=def")
    assert i.session_id == "abc"
    assert i.token == "def"


def test_parse_quoted_printable_breaks():
    c = GarminLiveTrackClient(hass=None, session=None)
    i = c.parse_livetrack_identity("https://livetrack.garmin.com/session/abc/token/de=\r\nf")
    assert i.token == "def"