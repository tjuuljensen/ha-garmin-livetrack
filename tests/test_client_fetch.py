from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import pytest

from custom_components.garmin_livetrack.client import GarminLiveTrackClient
from custom_components.garmin_livetrack.models import LiveTrackIdentity, LiveTrackSource


class FakeResponse:
    def __init__(self, *, status=200, text_data="", json_data=None):
        self.status = status
        self._text_data = text_data
        self._json_data = json_data if json_data is not None else {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def text(self):
        return self._text_data

    async def json(self, content_type=None):
        return self._json_data


class RecordingSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def get(self, url, timeout=None, headers=None):
        self.calls.append({"url": url, "headers": headers or {}})
        if not self._responses:
            raise AssertionError(f"No response configured for {url}")
        return self._responses.pop(0)


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


@pytest.mark.asyncio
async def test_fetch_trackpoints_builds_common_endpoint_with_begin():
    begin = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    session = RecordingSession(
        [
            FakeResponse(status=200, text_data='<meta name="csrf-token" content="csrf-1">'),
            FakeResponse(
                status=200,
                json_data={
                    "trackPoints": [
                        {"dateTime": "2026-01-01T00:01:00Z", "position": {"lat": 55.67, "lon": 12.56}}
                    ]
                },
            ),
        ]
    )
    client = GarminLiveTrackClient(hass=None, session=session)
    identity = LiveTrackIdentity(
        session_id="abc",
        token="secret",
        canonical_url="https://livetrack.garmin.com/session/abc/token/secret",
        redacted_url="https://livetrack.garmin.com/session/abc/token/sec...ret",
        source=LiveTrackSource.SERVICE,
    )

    result = await client.fetch_trackpoints(identity, begin=begin)

    assert result.ok is True
    assert len(session.calls) == 2
    parsed = urlparse(session.calls[1]["url"])
    assert parsed.path.endswith("/api/sessions/abc/track-points/common")
    params = parse_qs(parsed.query)
    assert params["token"] == ["secret"]
    assert params["begin"] == ["2026-01-01T00:00:00Z"]


@pytest.mark.asyncio
async def test_fetch_session_retries_once_after_403_with_refreshed_csrf():
    session = RecordingSession(
        [
            FakeResponse(status=200, text_data='<meta name="csrf-token" content="csrf-1">'),
            FakeResponse(status=403, json_data={}),
            FakeResponse(status=200, text_data='<meta name="csrf-token" content="csrf-2">'),
            FakeResponse(status=200, json_data={"sessionId": "abc", "userDisplayName": "Runner"}),
        ]
    )
    client = GarminLiveTrackClient(hass=None, session=session)
    identity = LiveTrackIdentity(
        session_id="abc",
        token="secret",
        canonical_url="https://livetrack.garmin.com/session/abc/token/secret",
        redacted_url="https://livetrack.garmin.com/session/abc/token/sec...ret",
        source=LiveTrackSource.SERVICE,
    )

    result = await client.fetch_session(identity)

    assert result.ok is True
    assert len(session.calls) == 4
    assert session.calls[1]["headers"]["Livetrack-Csrf-Token"] == "csrf-1"
    assert session.calls[3]["headers"]["Livetrack-Csrf-Token"] == "csrf-2"


@pytest.mark.asyncio
async def test_fetch_full_falls_back_to_hydration_when_trackpoint_endpoint_fails():
    html = '<script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"trackPoints":[{"dateTime":"2026-01-01T00:00:00Z","position":{"lat":55.67,"lon":12.56}}]}}}</script>'
    session = RecordingSession(
        [
            FakeResponse(status=200, text_data=f'<meta name="csrf-token" content="csrf-1">{html}'),
            FakeResponse(status=200, json_data={"sessionId": "abc", "userDisplayName": "Runner"}),
            FakeResponse(status=500, json_data={}),
        ]
    )
    client = GarminLiveTrackClient(hass=None, session=session)
    identity = LiveTrackIdentity(
        session_id="abc",
        token="secret",
        canonical_url="https://livetrack.garmin.com/session/abc/token/secret",
        redacted_url="https://livetrack.garmin.com/session/abc/token/sec...ret",
        source=LiveTrackSource.SERVICE,
    )

    result = await client.fetch_full(identity)

    assert result.ok is True
    assert result.trackpoint_count == 1
    assert result.source["trackpoints_source"] == "hydration"
    assert any(err.code == "trackpoints_http_error" for err in result.errors)


@pytest.mark.asyncio
async def test_fetch_trackpoints_retries_once_after_403_with_refreshed_csrf():
    session = RecordingSession(
        [
            FakeResponse(status=200, text_data='<meta name="csrf-token" content="csrf-1">'),
            FakeResponse(status=403, json_data={}),
            FakeResponse(status=200, text_data='<meta name="csrf-token" content="csrf-2">'),
            FakeResponse(
                status=200,
                json_data={
                    "trackPoints": [
                        {"dateTime": "2026-01-01T00:01:00Z", "position": {"lat": 55.67, "lon": 12.56}}
                    ]
                },
            ),
        ]
    )
    client = GarminLiveTrackClient(hass=None, session=session)
    identity = LiveTrackIdentity(
        session_id="abc",
        token="secret",
        canonical_url="https://livetrack.garmin.com/session/abc/token/secret",
        redacted_url="https://livetrack.garmin.com/session/abc/token/sec...ret",
        source=LiveTrackSource.SERVICE,
    )

    result = await client.fetch_trackpoints(identity)

    assert result.ok is True
    assert len(session.calls) == 4
    assert session.calls[1]["headers"]["Livetrack-Csrf-Token"] == "csrf-1"
    assert session.calls[3]["headers"]["Livetrack-Csrf-Token"] == "csrf-2"
