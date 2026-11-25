# Garmin LiveTrack Integration Architecture

## Purpose
This document describes the architecture and operational model for the `garmin_livetrack` integration.

The [README](../README.md) covers setup and operation. [TODO.md](../TODO.md) tracks development work.

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
- tokens for active-session recovery and full URLs in retained ended-session summaries
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

Explicit or inferred completion produces `ended`; manual stops produce `stopped`, runtime limits produce `expired`, and missing data or prolonged fetch failures can produce `stale`. `end_reason` records the cause. Manual stops emit `garmin_livetrack_session_updated`; finalization emits `garmin_livetrack_session_ended`.

### Retention
Per-user status sensors continue to present the latest ended-session data during the configured retention window. The retained summary is persisted and restored across Home Assistant restarts, so dashboards remain informative after a LiveTrack stops and after HA reboots.

## User Policy Model
### Global defaults and per-user overrides

Global settings control IMAP ingestion, user admission, activity filtering, polling, retention, and lifecycle timing. Per-user policy stores an enabled flag, handling mode, and optional activity filter.

User admission depends on `strict_users` and `accept_first_seen_users`; see the [approval matrix](ADVANCED.md#unknown-users-and-approval). Disabled-user policies are enforced only with `strict_users` enabled. A custom per-user activity filter overrides the global filter regardless of strict-user mode.

New names in the configured-users list become enabled policies. Existing policies retain their enabled state. Policy mode labels are stored metadata; admission uses the strict-user setting and enabled flag rather than treating mode as a separate runtime gate.

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
When Garmin publish-frequency gating is enabled (by `Adaptive` or an Advanced override), the coordinator:
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

Unknown activities are preserved and use a generic icon. They are accepted by the global `all` filter, but explicit global or per-user activity filters can reject them.

### Icon mapping
Icon selection is data-driven through `ACTIVITY_ICONS`. Active and inactive icon variants are chosen from the normalized activity value rather than from ad hoc branching logic.

### Metric normalization
The integration keeps Garmin raw point fields where useful and also derives normalized metrics for entity attributes and events:
- `speed_kmh`
- `pace_min_km`
- `distance_km`
- `duration_hms`
- `has_location`

When Garmin omits duration, distance, or speed, the coordinator can derive them from timestamps and GPS movement. Sparse points can retain earlier altitude, elevation gain, heart rate, power, and cadence. Optional `elevation_gain_m` comes from Garmin's `totalElevationGainMeters` or `elevationGainMeters`; it is not derived from altitude changes. Session diagnostics omit raw point data and coordinates.


## Transport Backoff
Transport protection is per session and transient. Each coordinator can track:
- `backoff_until`
- `consecutive_http_failures`
- `last_http_status`

Current backoff policy:
- `429`: exponential cooldown starting at 2 minutes, capped at 15 minutes
- `5xx` and retryable request failures: exponential cooldown starting at 30 seconds, capped at 10 minutes
- `403` after the CSRF retry path still fails: 1 minute initially, capped at 5 minutes
- successful fetch: clear backoff state

The backoff model delays Garmin requests, but it does not suspend lifecycle progression. No-progress, stale, ending, and finalization logic remain the controlling lifecycle layer.

## Diagnostics And Repairs
### Diagnostics
Diagnostics provide:
- configuration options and user display names (not anonymized)
- active and ended session counts
- user policy summaries
- session summaries
- effective User-Agent
- shape-change signal state

### Shape-change repair signal

The manager counts missing-session responses, missing trackpoints after a session has produced points, and sessions finalized with `no_trackpoints`. Repeated anomalies can raise a Home Assistant repair issue. Initial waits without trackpoints do not increment the signal on every poll.

Successful error-free fetches for sessions with existing points reduce the count. The signal is exposed through `sensor.garmin_livetrack_last_error` attributes and diagnostics.

## Logging And Debugging
### Startup diagnostics
Startup timing breadcrumbs are available through runtime state, diagnostics, and debug logs.

### Debug attributes
`last_fetch` remains exposed by design.

Additional troubleshooting attributes:
- `page_status`
- `api_status`
- `trackpoints_source`
- `poll_task_alive`

are gated behind `Expose debug attributes` in the `Advanced` options path. Adaptive scheduling and transport-backoff fields use the same toggle.

## Data Exposure

Status entities expose full LiveTrack URLs, including tokens. Recovery storage and retained ended-session summaries also contain sensitive session data. Event payloads omit URLs and tokens, but include coordinates and activity metrics. Diagnostics use redacted session URLs while retaining user names and policy settings.

See [Privacy and Security](../README.md#privacy-and-security) before sharing state exports or diagnostics.

## Release Model
Marketplace releases should map to explicit Git tags and GitHub releases. The repository should not treat arbitrary `main` commits as published release artifacts.

Marketplace retains the publisher tooling names `hacs.json` and `hacs/action`; see the [Home Assistant contributor documentation](https://contributors.home-assistant.io/glossary/hacs/). Keep these filenames and action references when updating user-facing Marketplace instructions.

Release checklist:
1. bump `manifest.json`, `pyproject.toml`, and the default User-Agent version together
2. update README and release notes for operator-facing changes
3. run the [local test suite](ADVANCED.md#local-testing) and check the Tests, HACS, and Hassfest workflows
4. create tag `vX.Y.Z`
5. publish a GitHub release from that tag
6. confirm the published version can be installed through Marketplace

This keeps installation state, diagnostics, and support discussions aligned to a concrete published artifact.
