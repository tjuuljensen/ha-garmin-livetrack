from __future__ import annotations

from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

ISSUE_SHAPE_CHANGE = "garmin_shape_change_suspected"


def async_sync_shape_change_issue(hass, *, suspected: bool, consecutive_anomaly_count: int) -> None:
    if suspected:
        ir.async_create_issue(
            hass,
            DOMAIN,
            ISSUE_SHAPE_CHANGE,
            is_fixable=False,
            is_persistent=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_SHAPE_CHANGE,
            translation_placeholders={
                "count": str(consecutive_anomaly_count),
            },
        )
        return

    ir.async_delete_issue(hass, DOMAIN, ISSUE_SHAPE_CHANGE)
