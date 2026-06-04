# Garmin LiveTrack Architecture And Implementation Plan

## Purpose
This document describes the current architecture, operational model, and remaining implementation work for the `garmin_livetrack` integration.

It is the maintainers' technical snapshot. The README is the operator-facing document. `TODO.md` is the active backlog.

## Scope
The integration provides:
- Garmin LiveTrack URL ingestion from Home Assistant services and IMAP events
- independent tracking of one or more sessions
- stable per-user entities
- restart recovery
- notification routing and message rendering
- diagnostics and repair signaling

## Architectural Model
### One integration entry
The integration is designed as one Home Assistant config entry that manages:
- one global/integration device
- one stable device per Garmin display name/user policy
- zero or more active runtime sessions
- zero or more retained ended sessions

### Runtime ownership
`GarminLiveTrackManager` owns:
- active coordinators
- retained ended sessions
- user policy state
- service registration
- IMAP listener registration
- storage load/save
- notification routing
- shape-change repair signaling

### Session ownership
Each active Garmin LiveTrack session has its own coordinator/task. Sessions do not share a poller or a shared mutable state object.

### Entity ownership
Entity ownership is split by purpose:
- global health and aggregate entities live on one integration device
- user-facing status/active/tracker entities live on user devices
- temporary fallback session devices are used only when a user identity is not yet known

## Runtime Components
### `client.py`
Responsibilities:
- parse and canonicalize LiveTrack URLs
- fetch Garmin page first
- extract CSRF when present
- call Garmin session API
- fall back to hydration payloads
- normalize session and trackpoint results
- return structured redacted errors

### `coordinator.py`
Responsibilities:
- manager lifecycle
- per-session polling
- startup recovery
- user policy enforcement
- notification routing
- retained ended-session handling
- repair-signal synchronization

### Entity platforms
- `sensor.py`
  - global sensors
  - per-user status sensors
- `binary_sensor.py`
  - global active sensor
  - per-user active sensors
- `device_tracker.py`
  - per-user GPS trackers

### `config_flow.py`
Responsibilities:
- initial setup
- global options
- per-user policy editing

### `repairs.py`
Responsibilities:
- create and clear Home Assistant repair issues for repeated Garmin anomaly patterns

## Storage Model
Persistent state lives in Home Assistant storage, not helper entities.

Stored data includes:
- recoverable active-session summaries
- token for restart recovery only
- known user policies
- retained ended-session summary state as needed for recovery behavior

The Garmin token remains in storage only and is redacted elsewhere.

## Session Lifecycle
### Main states
- `discovered`
- `fetching`
- `waiting_for_trackpoint`
- `active`
- `ending`
- `ended`

Other terminal or problem states:
- `expired`
- `stale`
- `stopped`
- `garmin_error`
- `rejected_user`
- `rejected_activity`

### End inference
The integration can finalize a session from:
- explicit Garmin END event
- Garmin end timestamp in the past
- fetch-ok but inactive/no-progress behavior
- manual stop

The user-facing terminal state remains `ended`. Differentiation is carried through `end_reason`.

### Retention
Per-user status sensors continue to present the latest ended-session data during the configured retention window. This keeps dashboards informative after a LiveTrack stops.

## User Policy Model
### Global defaults
Global settings define the default behavior for:
- notifications
- notify target
- iOS-style notification payload
- activity filter

### Per-user overrides
Per-user policy can override:
- tracking enabled
- handling mode
- notification enablement
- notify target
- iOS-style payload behavior
- activity filter

### Unknown-user handling
Behavior depends on:
- `strict_users`
- `accept_first_seen_users`

Supported paths:
- register and track immediately
- register only and reject tracking
- allow one event and require later explicit enablement

## Notifications
### Routing
Notification routing resolves in this order:
1. per-user override
2. global default

### Message rendering
Start and end notification text comes from configurable global templates.

Supported placeholders include:
- `user`
- `activity`
- `reason`
- `source`
- `url`
- `redacted_url`
- `session_id_hash`
- `distance_km`
- `duration_min`

If a template is invalid, the integration falls back to the default message and logs a warning.

## Garmin Fetch Strategy
### Request pattern
The request pattern is:
1. fetch public LiveTrack page
2. capture cookies and possible CSRF
3. request Garmin session API
4. inspect hydration payloads if necessary

### Trackpoint extraction
The parser searches for trackpoint-like content across likely data branches instead of depending on one fixed JSON path.

This includes:
- API arrays such as `trackPoints`, `trackpoints`, or `points`
- Next.js `__NEXT_DATA__`
- hydration or app-router payloads
- nested point-like arrays

## Diagnostics And Repairs
### Diagnostics
Diagnostics provide:
- redacted configuration
- active and ended session counts
- user policy summaries
- session summaries
- effective User-Agent
- shape-change signal state

### Shape-change repair signal
Repeated anomaly patterns such as:
- missing session
- missing trackpoints
- malformed response branches

can raise a Home Assistant repair issue indicating that Garmin's public response shape may have changed.

The same signal is also exposed through:
- `sensor.garmin_livetrack_last_error` attributes
- diagnostics

## Logging And Debugging
### Startup diagnostics
Startup timing breadcrumbs remain available through runtime state and debug logs. They no longer emit warning-level noise during normal operation.

### Debug attributes
`last_fetch` remains exposed by design.

Additional troubleshooting attributes:
- `page_status`
- `api_status`
- `trackpoints_source`
- `poll_task_alive`

are gated behind the normal `Expose debug attributes` option in the options UI.

## Product Decisions
The following decisions are currently intentional and closed:
- full LiveTrack URLs remain exposed on status entities
- `Expose debug attributes` remains in the normal options UI as an advanced troubleshooting toggle

## Remaining Work
### High-value remaining tests
- options-flow tests for user-policy editing
- per-user notification routing/fallback tests
- additional no-END discarded-activity coverage
- shape-change repair-signal transition tests
- configurable User-Agent tests

### Cleanup and migration
- evaluate whether a one-time entity-registry migration strategy is still needed
- add cleanup/migration tests if that path remains relevant

### Documentation
- keep README and TODO aligned with runtime behavior
- add focused migration guidance if entity-registry migration changes
