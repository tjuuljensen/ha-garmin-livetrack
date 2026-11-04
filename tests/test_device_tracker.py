"""Default map names and stable identities for Garmin trackers."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.garmin_livetrack.binary_sensor import GarminUserActiveBinarySensor
from custom_components.garmin_livetrack.device_tracker import GarminUserTracker
from custom_components.garmin_livetrack.sensor import GarminUserStatusSensor


def _manager():
    return SimpleNamespace(
        known_users={"thomas jensen": SimpleNamespace(name="Thomas Jensen")},
        sessions={},
        ended_sessions={},
    )


@pytest.mark.parametrize("phase", ["configured", "active", "ended"])
def test_user_device_name_is_consistent_across_platforms(phase):
    manager = _manager()
    now = datetime.now(UTC)
    session = SimpleNamespace(
        garmin_user="Thomas Jensen",
        first_seen=now,
        last_success=now,
        actual_end=now,
    )
    if phase == "active":
        manager.sessions["abc"] = SimpleNamespace(session=session)
    elif phase == "ended":
        manager.ended_sessions["abc"] = session

    tracker = GarminUserTracker(manager, "thomas jensen")
    entities = [
        tracker,
        GarminUserStatusSensor(manager, "thomas jensen"),
        GarminUserActiveBinarySensor(manager, "thomas jensen"),
    ]
    for entity in entities:
        assert entity.device_info["name"] == "Thomas Jensen"
        assert entity.device_info["identifiers"] == {
            ("garmin_livetrack", "user:efa381cc3fb7")
        }
    # The primary entity inherits the device name once in Home Assistant.
    assert tracker.has_entity_name is True
    assert tracker.name is None
    assert tracker.unique_id == "garmin_livetrack_user_tracker_efa381cc3fb7"


def test_unidentified_session_has_readable_name_and_same_identity():
    tracker = GarminUserTracker(_manager(), "session:abc")
    assert tracker.device_info["name"] == "Session ba7816bf"
    assert tracker.device_info["identifiers"] == {("garmin_livetrack", "session:abc")}
    assert tracker.unique_id == "garmin_livetrack_user_tracker_a29d803b477e"


async def test_existing_entity_id_and_user_name_survive_naming_change(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    registry = er.async_get(hass)
    await registry.async_load()
    existing = registry.async_get_or_create(
        "device_tracker",
        "garmin_livetrack",
        "garmin_livetrack_user_tracker_efa381cc3fb7",
        suggested_object_id="garmin_livetrack_thomas_jensen",
        original_name="Thomas Jensen",
        has_entity_name=False,
    )
    registry.async_update_entity(existing.entity_id, name="My running tracker")

    tracker = GarminUserTracker(_manager(), "thomas jensen")
    updated = registry.async_get_or_create(
        "device_tracker",
        "garmin_livetrack",
        tracker.unique_id,
        original_name=tracker.name,
        has_entity_name=tracker.has_entity_name,
        suggested_object_id="thomas_jensen",
    )
    assert updated.entity_id == "device_tracker.garmin_livetrack_thomas_jensen"
    assert updated.name == "My running tracker"
    assert len(registry.entities) == 1
    await hass.async_stop()
