# Garmin LiveTrack Integration Architecture

## Purpose
This document describes the current architecture, operational model, and remaining implementation work for the `garmin_livetrack` integration.

It is the maintainers' technical snapshot. The README is the operator-facing document. `TODO.md` is the active backlog.

## Scope
The integration provides:
- Garmin LiveTrack URL ingestion from Home Assistant services and IMAP events
- independent tracking of one or more sessions
- stable per-user entities
- restart recovery
- Home Assistant events for automation/package consumers
- layered Garmin fetch with dedicated incremental trackpoint polling
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
- call Garmin's dedicated incremental trackpoint endpoint
- fall back to hydration payloads
- normalize session and trackpoint results
- return structured redacted errors

### `coordinator.py`
Responsibilities:
- manager lifecycle
- per-session polling
- startup recovery
- user policy enforcement
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
- retained ended-session summary state for status-sensor presentation after restart

Recoverable active sessions store the token as a separate field. Retained ended-session summaries store the canonical URL so restored status sensors can keep exposing the full URL during the retention window.

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
Per-user status sensors continue to present the latest ended-session data during the configured retention window. The retained summary is persisted and restored across Home Assistant restarts, so dashboards remain informative after a LiveTrack stops and after HA reboots.

## User Policy Model
### Global defaults
Global settings define the default behavior for:
- activity filter

### Per-user overrides
Per-user policy can override:
- tracking enabled
- handling mode
- activity filter

### Unknown-user handling
Behavior depends on:
- `strict_users`
- `accept_first_seen_users`

Supported paths:
- register and track immediately
- register only and reject tracking
- allow one event and require later explicit enablement

## Notification Boundary
The integration does not send notifications directly.

It emits Home Assistant events and exposes entities, device trackers, and services.
Automations, scripts, blueprints, or YAML packages are responsible for turning those signals into notifications.

## Garmin Fetch Strategy
### Request pattern
The request pattern is:
1. fetch public LiveTrack page
2. capture cookies and possible CSRF
3. request Garmin session API for metadata
4. request Garmin incremental trackpoints
5. inspect payload and hydration fallbacks if necessary

### CSRF retry behavior
If Garmin responds with HTTP 403 from the session API or the incremental trackpoint endpoint, the client refreshes the public page once to refresh cookies/CSRF and retries exactly once before returning a structured error.

### Trackpoint extraction
The dedicated incremental endpoint is the preferred source for trackpoints. The parser fallback searches for trackpoint-like content across likely data branches instead of depending on one fixed JSON path.

This includes:
- API arrays such as `trackPoints`, `trackpoints`, or `points`
- Next.js `__NEXT_DATA__`
- hydration or app-router payloads
- nested point-like arrays

### Adaptive mode
When the update profile is `adaptive`, the coordinator:
- tracks Garmin `postTrackPointFrequency`
- computes `next_trackpoints_allowed_at`
- skips incremental trackpoint requests until Garmin is likely to have published a new point
- falls back to the effective metadata interval when Garmin does not provide a usable publishing frequency

This mode still preserves the existing lifecycle layer for stale/finalization/end inference.


## Normalization Model
### Activity normalization
Garmin is treated as the source of truth for activity names. The integration preserves the raw Garmin value as `activity_type_raw` and computes a normalized canonical value as `activity_type`.

Normalization rules:
- trim whitespace
- lowercase values
- replace spaces and dashes with underscores
- map known aliases through `ACTIVITY_ALIASES`
- preserve unknown values unchanged after normalization cleanup

Examples:
- `Trail Running` -> `running`
- `Mountain Biking` -> `cycling`
- `kayaking` -> `kayak`
- `adventure_racing` -> `adventure_racing`

Unknown activities are never rejected just because they are unknown. They remain visible in entities, events, and diagnostics and fall back to a generic icon.

### Icon mapping
Icon selection is data-driven through `ACTIVITY_ICONS`. Active and inactive icon variants are chosen from the normalized activity value rather than from ad hoc branching logic.

### Metric normalization
The integration keeps Garmin raw point fields where useful and also derives normalized metrics for entity attributes and events:
- `speed_kmh`
- `pace_min_km`
- `distance_km`
- `duration_hms`
- `has_location`

This keeps automation matching and dashboard presentation stable while preserving the underlying Garmin values for diagnostics.


### Transport backoff
Transport protection is per session and transient. Each coordinator can track:
- `backoff_until`
- `consecutive_http_failures`
- `last_http_status`

Current backoff policy:
- `429`: exponential cooldown starting at 2 minutes, capped at 15 minutes
- `5xx` and retryable request failures: exponential cooldown starting at 30 seconds, capped at 10 minutes
- `403` after the CSRF retry path still fails: moderate cooldown
- successful fetch: clear backoff state

The backoff model delays Garmin requests, but it does not suspend lifecycle progression. No-progress, stale, ending, and finalization logic remain the controlling lifecycle layer.

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
- additional no-END discarded-activity coverage
- shape-change repair-signal transition tests

### Entity-registry cleanup
- keep `garmin_livetrack.cleanup_legacy_entities` as an optional stale-registry cleanup tool
- add cleanup tests that verify active entities are not removed

### Documentation
- keep README and TODO aligned with runtime behavior
- keep cleanup guidance framed around generic entity-registry cleanup rather than one specific prior setup

## Release Model
HACS-facing releases should map to explicit Git tags and GitHub releases. The repository should not treat arbitrary `main` commits as published release artifacts.

Release checklist:
1. bump `manifest.json`, `pyproject.toml`, and the default User-Agent version together
2. update README if operator-facing behavior changed
3. create tag `vX.Y.Z`
4. publish a GitHub release from that tag
5. let HACS track the release

This keeps installation state, diagnostics, and support discussions aligned to a concrete published artifact.
